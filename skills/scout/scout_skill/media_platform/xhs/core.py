# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/media_platform/xhs/core.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import asyncio
import os
import random
from asyncio import Task
from typing import Dict, List, Optional

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)
from tenacity import RetryError

import config
from base.base_crawler import AbstractCrawler
from config import CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES
from model.m_xiaohongshu import NoteUrlInfo, CreatorUrlInfo
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import xhs as xhs_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var

from .client import XiaoHongShuClient
from .exception import DataFetchError
from .field import SearchSortType
from .help import parse_note_info_from_note_url, parse_creator_info_from_url, get_search_id
from .login import XiaoHongShuLogin


class XiaoHongShuCrawler(AbstractCrawler):
    context_page: Page
    xhs_client: XiaoHongShuClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        self.index_url = "https://www.xiaohongshu.com"
        # self.user_agent = utils.get_user_agent()
        self.user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        self.cdp_manager = None
        self.ip_proxy_pool = None  # Proxy IP pool for automatic proxy refresh

        # 🧠 接入“规则大脑”(Config) —— 注意：ConfigManager 内部已做绝对路径修复，不受 cwd 影响
        try:
            from core.config_loader import ConfigManager
            from core.db_manager import DBManager
        except ImportError:
            # 兜底：极端情况下 sys.path 不含项目根目录时，尝试再插入一次
            import os as _os
            import sys as _sys
            project_root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "../../../../.."))
            if project_root not in _sys.path:
                _sys.path.insert(0, project_root)
            from core.config_loader import ConfigManager
            from core.db_manager import DBManager

        self.rules = ConfigManager().load_scout_rules()
        utils.logger.info(f"[XiaoHongShuCrawler] 已加载配置规则: {self.rules}")

        # 💾 接入“记忆仓库”(DB)
        self.db = DBManager()
        utils.logger.info("[XiaoHongShuCrawler] 已初始化数据库管理器")

        # 将规则映射到原工程 config，尽量少改原抓取逻辑
        crawl_behavior = self.rules.get("crawl_behavior", {}) or {}
        headless_mode = bool(crawl_behavior.get("headless_mode", True))
        request_delay = float(crawl_behavior.get("request_delay", 2.0))
        max_notes_to_crawl = int(crawl_behavior.get("max_notes_to_crawl", 20))
        # 规则：滚动次数（用于首页“热身滚动”，更像真人；也可在弱网时触发更多资源加载）
        self.scroll_count = int(crawl_behavior.get("scroll_count", 0) or 0)

        # 规则：搜索排序（对齐到 MediaCrawler 的 config.SORT_TYPE）
        # scout_rules.yaml 支持：likes_descending / time_descending / comment_descending / general
        # MediaCrawler xhs_config 支持：popularity_descending / time_descending / comment_descending / general
        sort_by = str(crawl_behavior.get("sort_by", "") or "").strip()
        sort_map = {
            "likes_descending": "popularity_descending",
            "popularity_descending": "popularity_descending",
            "time_descending": "time_descending",
            "comment_descending": "comment_descending",
            "general": "general",
        }
        mapped_sort = sort_map.get(sort_by, sort_by)

        # ✅ 关键兜底：扫码登录在无头模式下无法完成，会导致“看似启动但一直不动”
        # 如果用户选择 qrcode 登录，则强制关闭 headless（除非用户已用 cookies 登录态）
        try:
            if getattr(config, "LOGIN_TYPE", "qrcode") == "qrcode" and headless_mode:
                utils.logger.warning(
                    "[XiaoHongShuCrawler] 检测到 login_type=qrcode 且 headless_mode=true，"
                    "为避免卡在扫码登录阶段，已自动强制 headless_mode=false"
                )
                headless_mode = False
        except Exception:
            # 不影响主流程
            pass

        config.HEADLESS = headless_mode
        config.CDP_HEADLESS = headless_mode
        config.CRAWLER_MAX_SLEEP_SEC = request_delay
        config.CRAWLER_MAX_NOTES_COUNT = max_notes_to_crawl
        if mapped_sort:
            # 同时写入 xhs_config 与总 config，保证 search() 的判断一致
            try:
                config.SORT_TYPE = mapped_sort
                from config import xhs_config as _xhs_cfg
                _xhs_cfg.SORT_TYPE = mapped_sort
                utils.logger.info(f"[XiaoHongShuCrawler] 已应用规则 sort_by={sort_by} -> SORT_TYPE={mapped_sort}")
            except Exception as e:
                utils.logger.warning(f"[XiaoHongShuCrawler] 应用 SORT_TYPE 失败: {e}")

        # 规则：清洗阈值 + 黑名单
        self.data_filters = self.rules.get("data_filters", {}) or {}
        self.blacklist_keywords = [str(x).strip() for x in (self.rules.get("blacklist_keywords") or []) if str(x).strip()]

    def _format_int_count(self, raw) -> int:
        """将点赞/评论等计数字段统一解析为 int（支持 '1.2万' / '10万+' / '5k' 等格式）"""
        if raw is None:
            return 0
        if isinstance(raw, (int, float)):
            return int(raw)
        s = str(raw).strip()
        if not s:
            return 0
        try:
            s_lower = s.lower()
            if "万+" in s or "w+" in s_lower:
                num = float(s.replace("万+", "").replace("w+", "").replace("W+", "").replace("+", ""))
                return int(num * 10000)
            if "万" in s or "w" in s_lower:
                num = float(s.replace("万", "").replace("w", "").replace("W", ""))
                return int(num * 10000)
            if "千" in s or "k" in s_lower:
                num = float(s.replace("千", "").replace("k", "").replace("K", ""))
                return int(num * 1000)
            return int(float(s))
        except Exception:
            return 0

    def _format_post_date(self, raw_time) -> str:
        """将 time 字段格式化为 YYYY-MM-DD（兼容秒/毫秒时间戳与字符串）"""
        if not raw_time:
            return ""
        try:
            from datetime import datetime
            if isinstance(raw_time, (int, float)):
                ts = raw_time
                # 兼容毫秒时间戳
                if ts > 1e10:
                    ts = ts / 1000
                return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            return str(raw_time)[:10]
        except Exception:
            return str(raw_time)

    def _format_tag_list(self, note_detail: Dict) -> str:
        """提取 note_detail.tag_list 里的话题名称，逗号拼接"""
        tags = []
        for tag in (note_detail.get("tag_list") or []):
            if isinstance(tag, dict) and tag.get("name"):
                tags.append(str(tag.get("name")))
        return ",".join(tags)

    def _is_valid_note_by_rules(self, note_detail: Dict) -> bool:
        """
        🧹 Python 层清洗：只有满足阈值的笔记才会入库/下载媒体/抓评论。
        规则来自 config/scout_rules.yaml:data_filters + blacklist_keywords
        """
        try:
            title = str(note_detail.get("title") or note_detail.get("display_title") or "").strip()
            content = str(note_detail.get("desc", "") or "").strip()

            # 黑名单过滤（标题+正文）
            hay = f"{title}\n{content}"
            for kw in self.blacklist_keywords:
                if kw and kw in hay:
                    return False

            # 字数阈值
            min_len = int(self.data_filters.get("min_content_length", 0) or 0)
            if min_len > 0 and len(content) < min_len:
                return False

            # 点赞阈值
            min_likes = int(self.data_filters.get("min_likes", 0) or 0)
            if min_likes > 0:
                interact_info = note_detail.get("interact_info", {}) or {}
                likes = self._format_int_count(interact_info.get("liked_count", 0))
                if likes < min_likes:
                    return False

            # 时间阈值（max_days_old）
            max_days_old = int(self.data_filters.get("max_days_old", 0) or 0)
            if max_days_old > 0:
                raw_time = note_detail.get("time")
                # raw_time 多为秒/毫秒时间戳
                if isinstance(raw_time, (int, float)) and raw_time:
                    ts = float(raw_time)
                    if ts > 1e10:
                        ts = ts / 1000
                    from datetime import datetime, timezone
                    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    now = datetime.now(tz=timezone.utc)
                    days = (now - dt).days
                    if days > max_days_old:
                        return False
        except Exception:
            # 规则过滤失败时不阻塞主流程：默认放行
            return True

        return True

    async def _save_note_to_core_db(self, note_detail: Dict) -> None:
        """
        💾 将笔记写入项目根目录的 database/redbook_core.db
        注意：DBManager 使用 INSERT OR IGNORE，遇到重复 note_id 会跳过（去重规则）。
        同时会爬取评论并保存到 raw_comments 字段（用于 VOC 分析）。
        """
        try:
            interact_info = note_detail.get("interact_info", {}) or {}
            note_id = note_detail.get("note_id") or ""
            if not note_id:
                return

            source_keyword = source_keyword_var.get() or ""
            title = note_detail.get("title") or note_detail.get("display_title") or (note_detail.get("desc", "")[:255])
            content = note_detail.get("desc", "") or ""
            likes = self._format_int_count(interact_info.get("liked_count", 0))
            comment_count = self._format_int_count(interact_info.get("comment_count", 0))
            post_date = self._format_post_date(note_detail.get("time"))
            tag_list = self._format_tag_list(note_detail)

            # 🗣️ 获取评论（用于 VOC 分析）
            raw_comments = ""
            try:
                max_comments = int(self.rules.get("crawl_behavior", {}).get("max_comments_per_note", 200))
                xsec_token = note_detail.get("xsec_token", "")
                if xsec_token:
                    utils.logger.info(f"[core_db] 开始获取评论 note_id={note_id}, max_comments={max_comments}")
                    comments_list = await self.xhs_client.get_note_all_comments(
                        note_id=note_id,
                        xsec_token=xsec_token,
                        crawl_interval=1.0,
                        callback=None,
                        max_count=max_comments,
                    )
                    # 提取评论内容并拼接
                    comment_texts = []
                    for comment in comments_list:
                        comment_content = comment.get("content", "").strip()
                        if comment_content:
                            comment_texts.append(comment_content)
                    raw_comments = "\n".join(comment_texts)
                    utils.logger.info(f"[core_db] 已获取 {len(comment_texts)} 条评论 note_id={note_id}")
            except Exception as e:
                utils.logger.warning(f"[core_db] 获取评论失败 note_id={note_id}: {e}")

            note_data = {
                "id": str(note_id),
                "title": str(title) if title is not None else "",
                "content": str(content) if content is not None else "",
                "likes": int(likes),
                "comment_count": int(comment_count),
                "post_date": post_date,
                "source_keyword": source_keyword,
                "tag_list": tag_list,
                "raw_comments": raw_comments,
            }

            inserted = self.db.add_note(note_data)
            if inserted:
                utils.logger.info(f"[core_db] 已入库 note_id={note_id}, keyword={source_keyword}, likes={likes}, comments={comment_count}, raw_comments_len={len(raw_comments)}")
            else:
                # 如果笔记已存在，更新评论字段
                if raw_comments:
                    self.db.update_note(str(note_id), {"raw_comments": raw_comments})
                    utils.logger.info(f"[core_db] 已更新评论 note_id={note_id}, raw_comments_len={len(raw_comments)}")
        except Exception as e:
            utils.logger.warning(f"[core_db] 入库失败: {e}")

    async def start(self) -> None:
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            self.ip_proxy_pool = await create_ip_pool(config.IP_PROXY_POOL_COUNT, enable_validate_ip=True)
            ip_proxy_info: IpInfoModel = await self.ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

        async with async_playwright() as playwright:
            # Choose launch mode based on configuration
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[XiaoHongShuCrawler] Launching browser using CDP mode")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    self.user_agent,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[XiaoHongShuCrawler] Launching browser using standard mode")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    playwright_proxy_format,
                    self.user_agent,
                    headless=config.HEADLESS,
                )
                # stealth.min.js is a js script to prevent the website from detecting the crawler.
                await self.browser_context.add_init_script(path="libs/stealth.min.js")

            self.context_page = await self.browser_context.new_page()
            # 🧠 规则驱动：站点首页加载在部分网络环境可能超时，允许通过规则配置提升容错
            crawl_behavior = self.rules.get("crawl_behavior", {}) or {}
            goto_timeout_ms = int(crawl_behavior.get("goto_timeout_ms", 90000))
            await self.context_page.goto(self.index_url, timeout=goto_timeout_ms, wait_until="domcontentloaded")

            # 🧭 规则驱动：首页“热身滚动”（可选）
            # 注意：search 模式主要走 API，但热身滚动有时能让页面/脚本更充分加载，且更像真人。
            if getattr(self, "scroll_count", 0) > 0:
                try:
                    for _ in range(int(self.scroll_count)):
                        await asyncio.sleep(random.uniform(0.2, 0.6))
                        await self.context_page.mouse.wheel(0, random.randint(500, 1200))
                    utils.logger.info(f"[XiaoHongShuCrawler] 首页热身滚动完成：scroll_count={self.scroll_count}")
                except Exception as e:
                    utils.logger.warning(f"[XiaoHongShuCrawler] 热身滚动失败（忽略继续）：{e}")

            # Create a client to interact with the Xiaohongshu website.
            self.xhs_client = await self.create_xhs_client(httpx_proxy_format)
            if not await self.xhs_client.pong():
                login_obj = XiaoHongShuLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # input your phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.xhs_client.update_cookies(browser_context=self.browser_context)

            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                # Search for notes and retrieve their comment information.
                await self.search()
            elif config.CRAWLER_TYPE == "detail":
                # Get the information and comments of the specified post
                await self.get_specified_notes()
            elif config.CRAWLER_TYPE == "creator":
                # Get creator's information and their notes and comments
                await self.get_creators_and_notes()
            else:
                pass

            utils.logger.info("[XiaoHongShuCrawler.start] Xhs Crawler finished ...")

    async def search(self) -> None:
        """Search for notes and retrieve their comment information."""
        utils.logger.info("[XiaoHongShuCrawler.search] Begin search Xiaohongshu keywords")
        xhs_limit_count = 20  # Xiaohongshu limit page fixed value
        if config.CRAWLER_MAX_NOTES_COUNT < xhs_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = xhs_limit_count
        start_page = config.START_PAGE
        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(f"[XiaoHongShuCrawler.search] Current search keyword: {keyword}")
            page = 1
            search_id = get_search_id()
            while (page - start_page + 1) * xhs_limit_count <= config.CRAWLER_MAX_NOTES_COUNT:
                if page < start_page:
                    utils.logger.info(f"[XiaoHongShuCrawler.search] Skip page {page}")
                    page += 1
                    continue

                try:
                    utils.logger.info(f"[XiaoHongShuCrawler.search] search Xiaohongshu keyword: {keyword}, page: {page}")
                    note_ids: List[str] = []
                    xsec_tokens: List[str] = []
                    # 处理排序类型：评论数排序需要本地排序，其他使用API排序
                    api_sort = SearchSortType.GENERAL
                    if config.SORT_TYPE == "comment_descending":
                        # 评论数排序：使用时间排序获取数据，然后在本地按评论数排序
                        api_sort = SearchSortType.TIME_DESC
                    elif config.SORT_TYPE == "popularity_descending":
                        api_sort = SearchSortType.MOST_POPULAR
                    elif config.SORT_TYPE == "time_descending":
                        api_sort = SearchSortType.LATEST
                    elif config.SORT_TYPE != "":
                        api_sort = SearchSortType(config.SORT_TYPE)
                    
                    notes_res = await self.xhs_client.get_note_by_keyword(
                        keyword=keyword,
                        search_id=search_id,
                        page=page,
                        sort=api_sort,
                    )
                    utils.logger.info(f"[XiaoHongShuCrawler.search] Search notes response: {notes_res}")
                    if not notes_res or not notes_res.get("has_more", False):
                        utils.logger.info("[XiaoHongShuCrawler.search] No more content!")
                        break
                    
                    # 获取笔记详情
                    semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
                    task_list = [
                        self.get_note_detail_async_task(
                            note_id=post_item.get("id"),
                            xsec_source=post_item.get("xsec_source"),
                            xsec_token=post_item.get("xsec_token"),
                            semaphore=semaphore,
                        ) for post_item in notes_res.get("items", {}) if post_item.get("model_type") not in ("rec_query", "hot_query")
                    ]
                    note_details = await asyncio.gather(*task_list)
                    
                    # 如果是评论数排序，在本地对结果进行排序
                    if config.SORT_TYPE == "comment_descending":
                        # 过滤掉None值，然后按评论数降序排序
                        note_details = [nd for nd in note_details if nd is not None]
                        
                        def parse_count(count_str):
                            """解析评论数字符串，转换为整数用于排序"""
                            if not count_str:
                                return 0
                            if isinstance(count_str, (int, float)):
                                return int(count_str)
                            count_str = str(count_str).strip()
                            try:
                                # 处理"1.2万"、"5k"等格式
                                if "万" in count_str or "w" in count_str.lower():
                                    num = float(count_str.replace("万", "").replace("w", "").replace("W", ""))
                                    return int(num * 10000)
                                elif "k" in count_str.lower() or "千" in count_str:
                                    num = float(count_str.replace("k", "").replace("K", "").replace("千", ""))
                                    return int(num * 1000)
                                else:
                                    # 尝试直接转换为整数
                                    return int(float(count_str))
                            except (ValueError, AttributeError):
                                return 0
                        
                        note_details.sort(
                            key=lambda x: parse_count(x.get("comment_count", "0")),
                            reverse=True
                        )
                        utils.logger.info(f"[XiaoHongShuCrawler.search] Sorted {len(note_details)} notes by comment count (descending)")
                    for note_detail in note_details:
                        if note_detail:
                            # 🧹 规则过滤：不满足阈值则跳过（不入库/不下载媒体/不抓评论）
                            if not self._is_valid_note_by_rules(note_detail):
                                try:
                                    nid = note_detail.get("note_id") or ""
                                    utils.logger.info(f"[XiaoHongShuCrawler.search] Skip note by rules: note_id={nid}")
                                except Exception:
                                    pass
                                continue
                            await xhs_store.update_xhs_note(note_detail)
                            # 💾 同步写入"项目根目录记忆仓库"SQLite（database/redbook_core.db）
                            await self._save_note_to_core_db(note_detail)
                            await self.get_notice_media(note_detail)
                            note_ids.append(note_detail.get("note_id"))
                            xsec_tokens.append(note_detail.get("xsec_token"))
                    page += 1
                    utils.logger.info(f"[XiaoHongShuCrawler.search] Note details: {note_details}")
                    await self.batch_get_note_comments(note_ids, xsec_tokens)

                    # Sleep after each page navigation
                    await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                    utils.logger.info(f"[XiaoHongShuCrawler.search] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after page {page-1}")
                except DataFetchError:
                    utils.logger.error("[XiaoHongShuCrawler.search] Get note detail error")
                    break

    async def get_creators_and_notes(self) -> None:
        """Get creator's notes and retrieve their comment information."""
        utils.logger.info("[XiaoHongShuCrawler.get_creators_and_notes] Begin get Xiaohongshu creators")
        for creator_url in config.XHS_CREATOR_ID_LIST:
            try:
                # Parse creator URL to get user_id and security tokens
                creator_info: CreatorUrlInfo = parse_creator_info_from_url(creator_url)
                utils.logger.info(f"[XiaoHongShuCrawler.get_creators_and_notes] Parse creator URL info: {creator_info}")
                user_id = creator_info.user_id

                # get creator detail info from web html content
                createor_info: Dict = await self.xhs_client.get_creator_info(
                    user_id=user_id,
                    xsec_token=creator_info.xsec_token,
                    xsec_source=creator_info.xsec_source
                )
                if createor_info:
                    await xhs_store.save_creator(user_id, creator=createor_info)
            except ValueError as e:
                utils.logger.error(f"[XiaoHongShuCrawler.get_creators_and_notes] Failed to parse creator URL: {e}")
                continue

            # Use fixed crawling interval
            crawl_interval = config.CRAWLER_MAX_SLEEP_SEC
            # Get all note information of the creator
            all_notes_list = await self.xhs_client.get_all_notes_by_creator(
                user_id=user_id,
                crawl_interval=crawl_interval,
                callback=self.fetch_creator_notes_detail,
                xsec_token=creator_info.xsec_token,
                xsec_source=creator_info.xsec_source,
            )

            note_ids = []
            xsec_tokens = []
            for note_item in all_notes_list:
                note_ids.append(note_item.get("note_id"))
                xsec_tokens.append(note_item.get("xsec_token"))
            await self.batch_get_note_comments(note_ids, xsec_tokens)

    async def fetch_creator_notes_detail(self, note_list: List[Dict]):
        """Concurrently obtain the specified post list and save the data"""
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [
            self.get_note_detail_async_task(
                note_id=post_item.get("note_id"),
                xsec_source=post_item.get("xsec_source"),
                xsec_token=post_item.get("xsec_token"),
                semaphore=semaphore,
            ) for post_item in note_list
        ]

        note_details = await asyncio.gather(*task_list)
        for note_detail in note_details:
            if note_detail:
                if not self._is_valid_note_by_rules(note_detail):
                    continue
                await xhs_store.update_xhs_note(note_detail)
                await self._save_note_to_core_db(note_detail)
                await self.get_notice_media(note_detail)

    async def get_specified_notes(self):
        """Get the information and comments of the specified post

        Note: Must specify note_id, xsec_source, xsec_token
        """
        get_note_detail_task_list = []
        for full_note_url in config.XHS_SPECIFIED_NOTE_URL_LIST:
            note_url_info: NoteUrlInfo = parse_note_info_from_note_url(full_note_url)
            utils.logger.info(f"[XiaoHongShuCrawler.get_specified_notes] Parse note url info: {note_url_info}")
            crawler_task = self.get_note_detail_async_task(
                note_id=note_url_info.note_id,
                xsec_source=note_url_info.xsec_source,
                xsec_token=note_url_info.xsec_token,
                semaphore=asyncio.Semaphore(config.MAX_CONCURRENCY_NUM),
            )
            get_note_detail_task_list.append(crawler_task)

        need_get_comment_note_ids = []
        xsec_tokens = []
        note_details = await asyncio.gather(*get_note_detail_task_list)
        for note_detail in note_details:
            if note_detail:
                if not self._is_valid_note_by_rules(note_detail):
                    continue
                need_get_comment_note_ids.append(note_detail.get("note_id", ""))
                xsec_tokens.append(note_detail.get("xsec_token", ""))
                await xhs_store.update_xhs_note(note_detail)
                await self._save_note_to_core_db(note_detail)
                await self.get_notice_media(note_detail)
        await self.batch_get_note_comments(need_get_comment_note_ids, xsec_tokens)

    async def get_note_detail_async_task(
        self,
        note_id: str,
        xsec_source: str,
        xsec_token: str,
        semaphore: asyncio.Semaphore,
    ) -> Optional[Dict]:
        """Get note detail

        Args:
            note_id:
            xsec_source:
            xsec_token:
            semaphore:

        Returns:
            Dict: note detail
        """
        note_detail = None
        utils.logger.info(f"[get_note_detail_async_task] Begin get note detail, note_id: {note_id}")
        async with semaphore:
            try:
                try:
                    note_detail = await self.xhs_client.get_note_by_id(note_id, xsec_source, xsec_token)
                except RetryError:
                    pass

                if not note_detail:
                    note_detail = await self.xhs_client.get_note_by_id_from_html(note_id, xsec_source, xsec_token,
                                                                                 enable_cookie=True)
                    if not note_detail:
                        raise Exception(f"[get_note_detail_async_task] Failed to get note detail, Id: {note_id}")

                note_detail.update({"xsec_token": xsec_token, "xsec_source": xsec_source})

                # Sleep after fetching note detail
                await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                utils.logger.info(f"[get_note_detail_async_task] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching note {note_id}")

                return note_detail

            except DataFetchError as ex:
                utils.logger.error(f"[XiaoHongShuCrawler.get_note_detail_async_task] Get note detail error: {ex}")
                return None
            except KeyError as ex:
                utils.logger.error(f"[XiaoHongShuCrawler.get_note_detail_async_task] have not fund note detail note_id:{note_id}, err: {ex}")
                return None

    async def batch_get_note_comments(self, note_list: List[str], xsec_tokens: List[str]):
        """Batch get note comments"""
        if not config.ENABLE_GET_COMMENTS:
            utils.logger.info(f"[XiaoHongShuCrawler.batch_get_note_comments] Crawling comment mode is not enabled")
            return

        utils.logger.info(f"[XiaoHongShuCrawler.batch_get_note_comments] Begin batch get note comments, note list: {note_list}")
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list: List[Task] = []
        for index, note_id in enumerate(note_list):
            task = asyncio.create_task(
                self.get_comments(note_id=note_id, xsec_token=xsec_tokens[index], semaphore=semaphore),
                name=note_id,
            )
            task_list.append(task)
        await asyncio.gather(*task_list)

    async def get_comments(self, note_id: str, xsec_token: str, semaphore: asyncio.Semaphore):
        """Get note comments with keyword filtering and quantity limitation"""
        async with semaphore:
            utils.logger.info(f"[XiaoHongShuCrawler.get_comments] Begin get note id comments {note_id}")
            # Use fixed crawling interval
            crawl_interval = config.CRAWLER_MAX_SLEEP_SEC
            await self.xhs_client.get_note_all_comments(
                note_id=note_id,
                xsec_token=xsec_token,
                crawl_interval=crawl_interval,
                callback=xhs_store.batch_update_xhs_note_comments,
                max_count=CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
            )

            # Sleep after fetching comments
            await asyncio.sleep(crawl_interval)
            utils.logger.info(f"[XiaoHongShuCrawler.get_comments] Sleeping for {crawl_interval} seconds after fetching comments for note {note_id}")

    async def create_xhs_client(self, httpx_proxy: Optional[str]) -> XiaoHongShuClient:
        """Create Xiaohongshu client"""
        utils.logger.info("[XiaoHongShuCrawler.create_xhs_client] Begin create Xiaohongshu API client ...")
        cookie_str, cookie_dict = utils.convert_cookies(await self.browser_context.cookies())
        xhs_client_obj = XiaoHongShuClient(
            proxy=httpx_proxy,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-CN,zh;q=0.9",
                "cache-control": "no-cache",
                "content-type": "application/json;charset=UTF-8",
                "origin": "https://www.xiaohongshu.com",
                "pragma": "no-cache",
                "priority": "u=1, i",
                "referer": "https://www.xiaohongshu.com/",
                "sec-ch-ua": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-site",
                "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
                "Cookie": cookie_str,
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
            proxy_ip_pool=self.ip_proxy_pool,  # Pass proxy pool for automatic refresh
        )
        return xhs_client_obj

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        utils.logger.info("[XiaoHongShuCrawler.launch_browser] Begin create browser context ...")
        if config.SAVE_LOGIN_STATE:
            # feat issue #14
            # we will save login state to avoid login every time
            user_data_dir = os.path.join(os.getcwd(), "browser_data", config.USER_DATA_DIR % config.PLATFORM)  # type: ignore
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=playwright_proxy,  # type: ignore
                viewport={
                    "width": 1920,
                    "height": 1080
                },
                user_agent=user_agent,
            )
            return browser_context
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy)  # type: ignore
            browser_context = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=user_agent)
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser using CDP mode"""
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # Display browser information
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[XiaoHongShuCrawler] CDP browser info: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[XiaoHongShuCrawler] CDP mode launch failed, falling back to standard mode: {e}")
            # Fall back to standard mode
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def close(self):
        """Close browser context"""
        # Special handling if using CDP mode
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[XiaoHongShuCrawler.close] Browser context closed ...")

    async def get_notice_media(self, note_detail: Dict):
        if not config.ENABLE_GET_MEIDAS:
            utils.logger.info(f"[XiaoHongShuCrawler.get_notice_media] Crawling image mode is not enabled")
            return
        # 只下载封面图片，不下载视频
        await self.get_note_images(note_detail)
        # await self.get_notice_video(note_detail)  # 禁用视频下载

    async def get_note_images(self, note_item: Dict):
        """Get note images - only download cover (first image). Please use get_notice_media

        Args:
            note_item: Note item dictionary
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        note_id = note_item.get("note_id")
        image_list: List[Dict] = note_item.get("image_list", [])

        for img in image_list:
            if img.get("url_default") != "":
                img.update({"url": img.get("url_default")})

        if not image_list:
            return
        
        # 只下载封面图片（第一张）
        cover_pic = image_list[0]
        url = cover_pic.get("url")
        if not url:
            return
        
        utils.logger.info(f"[XiaoHongShuCrawler.get_note_images] Downloading cover image for note {note_id}")
        content = await self.xhs_client.get_note_media(url)
        await asyncio.sleep(random.random())
        if content is None:
            return
        
        extension_file_name = "cover.jpg"
        await xhs_store.update_xhs_note_image(note_id, content, extension_file_name)
        utils.logger.info(f"[XiaoHongShuCrawler.get_note_images] Cover image saved for note {note_id}")

    async def get_notice_video(self, note_item: Dict):
        """Get note videos. Please use get_notice_media

        Args:
            note_item: Note item dictionary
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        note_id = note_item.get("note_id")

        videos = xhs_store.get_video_url_arr(note_item)

        if not videos:
            return
        videoNum = 0
        for url in videos:
            content = await self.xhs_client.get_note_media(url)
            await asyncio.sleep(random.random())
            if content is None:
                continue
            extension_file_name = f"{videoNum}.mp4"
            videoNum += 1
            await xhs_store.update_xhs_note_video(note_id, content, extension_file_name)
