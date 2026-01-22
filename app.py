import os
import subprocess
import sys

# === 自动安装 Playwright 浏览器的补丁 ===
def install_playwright():
    try:
        # 尝试导入 playwright，看看浏览器是否已安装
        from playwright.sync_api import sync_playwright
        # 这里只是简单检查，实际上如果没有浏览器二进制文件，运行通常会报错
        # 为了保险，我们直接运行 install 命令，playwright 会自己判断是否需要下载
        print("正在检查/安装 Playwright 浏览器内核...")
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        print("Playwright 浏览器内核安装完成！")
    except Exception as e:
        print(f"安装 Playwright 浏览器失败: {e}")

# 在程序启动时运行安装
install_playwright()
# ==========================================

# 下面才是你原本的代码
import streamlit as st
# ...




# -*- coding: utf-8 -*-
"""Streamlit 4-stage Pipeline App

Pages:
- 🛸 Scout: DuckDuckGo 搜索 -> 勾选 -> Jina Reader 深度挖掘
- 🧠 Analyst: 基于素材生成策略报告
- ✍️ Writer: 草稿 -> 人工定稿
- 🎨 Visual: 基于定稿生成视觉策划（tabs 展示）

运行：
  streamlit run app.py

说明：
- 需要环境变量 GEMINI_API_KEY（Analyst/Writer/Visual 调用 Gemini）
"""

from __future__ import annotations

import os
import sys
import time
import subprocess
import signal
import csv
import io
import sqlite3
import json
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st


# 确保项目根目录在 sys.path（支持从任意位置 streamlit run）
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _init_session_state() -> None:
    """程序入口：初始化全局状态变量。"""
    defaults = {
        "scout_results": [],  # Scout 搜到的原始列表
        "selected_materials": [],  # Deep Dive 后的全文素材
        "strategy_report": "",  # Analyst 生成的策略
        "final_copy": "",  # Writer 定稿的文案
        "visual_plan": [],  # Visual 生成的 Prompt（结构化列表）
        "_scout_error": "",
        "_scout_searched": False,
        "_xhs_job": None,  # {"pid": int, "log_path": str, "start_ts": float, "start_count": int, "keyword": str}
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    
    # ✅ 自动从环境变量读取 GEMINI_API_KEY（如果已设置）
    if "_gemini_api_key" not in st.session_state:
        env_key = os.getenv("GEMINI_API_KEY", "").strip()
        if env_key:
            st.session_state["_gemini_api_key"] = env_key
            # 确保环境变量已设置（可能用户是在 Streamlit 启动后才 export 的）
            os.environ["GEMINI_API_KEY"] = env_key
    
    # ✅ 自动应用环境变量中的代理设置
    _auto_apply_proxy_from_env()


def _load_user_settings() -> Dict[str, str]:
    """从配置文件加载用户设置（预设默认值）。"""
    settings_path = Path(__file__).parent / "config" / "user_settings.json"
    try:
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                return settings
    except Exception as e:
        print(f"⚠️ 加载用户设置失败：{e}")
    return {
        "gemini_api_key": "",
        "https_proxy": "",
        "http_proxy": "",
        "all_proxy": "",
        "gemini_transport": "rest",
    }

def _save_user_settings(settings: Dict[str, str]) -> None:
    """保存用户设置到配置文件。"""
    settings_path = Path(__file__).parent / "config" / "user_settings.json"
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        print(f"✅ 用户设置已保存到：{settings_path}")
    except Exception as e:
        print(f"⚠️ 保存用户设置失败：{e}")

def _auto_apply_proxy_from_env() -> None:
    """
    自动应用环境变量和配置文件中的代理设置（如果存在）。
    确保代理设置在整个 Streamlit 会话中生效。
    """
    # 1. 先加载用户设置（预设默认值）
    user_settings = _load_user_settings()
    
    # 2. 优先使用环境变量，如果没有则使用配置文件中的预设值
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or user_settings.get("https_proxy", "")
    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or user_settings.get("http_proxy", "")
    all_proxy = os.getenv("ALL_PROXY") or os.getenv("all_proxy") or user_settings.get("all_proxy", "")
    gemini_transport = os.getenv("GEMINI_TRANSPORT") or user_settings.get("gemini_transport", "rest")
    
    # 确保同时设置大小写版本（兼容不同库）
    if https_proxy:
        os.environ["HTTPS_PROXY"] = https_proxy
        os.environ["https_proxy"] = https_proxy
        print(f"✅ 已自动应用 HTTPS_PROXY: {https_proxy}")
    if http_proxy:
        os.environ["HTTP_PROXY"] = http_proxy
        os.environ["http_proxy"] = http_proxy
        print(f"✅ 已自动应用 HTTP_PROXY: {http_proxy}")
    if all_proxy:
        os.environ["ALL_PROXY"] = all_proxy
        os.environ["all_proxy"] = all_proxy
        print(f"✅ 已自动应用 ALL_PROXY: {all_proxy}")
    os.environ["GEMINI_TRANSPORT"] = gemini_transport
    print(f"✅ 已自动应用 GEMINI_TRANSPORT: {gemini_transport}")
    
    # 3. 自动应用 Gemini API Key（如果配置文件中有）
    gemini_key = os.getenv("GEMINI_API_KEY") or user_settings.get("gemini_api_key", "")
    if gemini_key:
        os.environ["GEMINI_API_KEY"] = gemini_key
        print(f"✅ 已自动应用 GEMINI_API_KEY（从预设配置）")


def _safe_truncate(text: str, n: int = 220) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


def ddg_search(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """DuckDuckGo 搜索。

    优先使用 duckduckgo_search（若已安装）；否则退化为抓取 DDG HTML 结果。
    返回字段：title, url, snippet
    """
    q = (query or "").strip()
    if not q:
        return []

    last_err: Exception | None = None

    # 1) 优先：新的 ddgs 包（duckduckgo_search 的重命名版本）
    try:
        from ddgs import DDGS  # type: ignore
        import time

        results: List[Dict[str, str]] = []
        max_retries = 2
        for attempt in range(max_retries):
            try:
                with DDGS() as ddgs:
                    count = 0
                    try:
                        for r in ddgs.text(q, max_results=max_results):
                            if r is None:
                                continue
                            title = str(r.get("title", "") or "").strip()
                            url = str(r.get("href", "") or r.get("url", "") or "").strip()
                            snippet = str(r.get("body", "") or r.get("snippet", "") or "").strip()
                            if url:
                                results.append({"title": title, "url": url, "snippet": snippet})
                                count += 1
                    except StopIteration:
                        pass
                    except Exception as inner_e:
                        if attempt == max_retries - 1:
                            last_err = inner_e
                        break
                    
                    if count > 0:
                        return results
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    if last_err is None:
                        last_err = RuntimeError("ddgs 返回空结果（可能被反爬或网络受限）")
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                last_err = e
                break
    except ImportError:
        # 尝试旧的 duckduckgo_search 包
        try:
            from duckduckgo_search import DDGS  # type: ignore
            import time

            results: List[Dict[str, str]] = []
            max_retries = 2
            for attempt in range(max_retries):
                try:
                    with DDGS(timeout=20) as ddgs:
                        count = 0
                        try:
                            for r in ddgs.text(q, max_results=max_results):
                                if r is None:
                                    continue
                                title = str(r.get("title", "") or "").strip()
                                url = str(r.get("href", "") or r.get("url", "") or "").strip()
                                snippet = str(r.get("body", "") or r.get("snippet", "") or "").strip()
                                if url:
                                    results.append({"title": title, "url": url, "snippet": snippet})
                                    count += 1
                        except StopIteration:
                            pass
                        except Exception as inner_e:
                            if attempt == max_retries - 1:
                                last_err = inner_e
                            break
                        
                        if count > 0:
                            return results
                        if attempt < max_retries - 1:
                            time.sleep(1)
                            continue
                        if last_err is None:
                            last_err = RuntimeError("duckduckgo_search 返回空结果")
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(1)
                        continue
                    last_err = e
                    break
        except ImportError:
            pass
    except Exception as e:
        last_err = e

    # 2) 退化：直接请求 HTML（尽量用 parsel 解析；否则用极简解析）
    try:
        import requests

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            )
        }
        resp = requests.get(
            "https://duckduckgo.com/html/",
            params={"q": q},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        html = resp.text

        try:
            from parsel import Selector  # type: ignore

            sel = Selector(text=html)
            items = sel.css("div.results > div.result")
            out: List[Dict[str, str]] = []
            for it in items[:max_results]:
                a = it.css("a.result__a")
                title = (a.css("::text").get() or "").strip()
                url = (a.attrib.get("href") or "").strip()
                snippet = (it.css("a.result__snippet::text").get() or "").strip()
                if url:
                    out.append({"title": title, "url": url, "snippet": snippet})
            return out
        except Exception:
            out: List[Dict[str, str]] = []
            marker = "result__a"
            idx = 0
            while len(out) < max_results:
                idx = html.find(marker, idx)
                if idx == -1:
                    break
                href_i = html.rfind('href="', 0, idx)
                if href_i == -1:
                    idx += len(marker)
                    continue
                href_i += len('href="')
                href_j = html.find('"', href_i)
                url = html[href_i:href_j].strip() if href_j != -1 else ""
                title_i = html.find(">", idx)
                title_j = html.find("</a>", title_i)
                title = ""
                if title_i != -1 and title_j != -1:
                    title = (
                        html[title_i + 1 : title_j]
                        .replace("&amp;", "&")
                        .replace("&quot;", '"')
                        .strip()
                    )
                if url:
                    out.append({"title": title, "url": url, "snippet": ""})
                idx += len(marker)
            return out
    except Exception:
        if last_err is not None:
            raise RuntimeError(f"DuckDuckGo 搜索失败：{last_err}") from last_err
        raise RuntimeError("DuckDuckGo 搜索失败：网络或依赖不可用")


def fetch_fulltext_via_jina(url: str) -> str:
    """Jina Reader 获取正文（r.jina.ai）。"""
    u = (url or "").strip()
    if not u:
        return ""

    if u.startswith("https://") or u.startswith("http://"):
        reader_url = "https://r.jina.ai/" + u
    else:
        reader_url = "https://r.jina.ai/https://" + u

    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    resp = requests.get(reader_url, headers=headers, timeout=25)
    resp.raise_for_status()
    return (resp.text or "").strip()


def _render_header() -> None:
    st.set_page_config(page_title="AI 小红书流水线", page_icon="🧩", layout="wide")
    st.title("🧩 AI 小红书内容流水线")
    st.caption("🛸 侦察 → 🧠 分析 → ✍️ 写作 → 🎨 视觉")

    # =========================
    # 🌐 网络/代理（自动应用环境变量）
    # =========================
    # ✅ 自动应用环境变量中的代理设置（如果存在）
    _auto_apply_proxy_from_env()
    
    with st.sidebar.expander("🌐 网络/代理（预设默认）", expanded=False):
        # 加载用户设置（预设默认值）
        user_settings = _load_user_settings()
        
        # 显示当前已应用的代理设置（优先环境变量，其次预设值）
        current_https = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or user_settings.get("https_proxy", "")
        current_http = os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or user_settings.get("http_proxy", "")
        current_all = os.getenv("ALL_PROXY") or os.getenv("all_proxy") or user_settings.get("all_proxy", "")
        current_transport = os.getenv("GEMINI_TRANSPORT") or user_settings.get("gemini_transport", "rest")
        
        if current_https or current_http or current_all:
            st.info(f"✅ 已自动应用预设默认值")
            if current_https:
                st.caption(f"HTTPS_PROXY: {current_https}")
            if current_http:
                st.caption(f"HTTP_PROXY: {current_http}")
            if current_all:
                st.caption(f"ALL_PROXY: {current_all}")
            st.caption(f"GEMINI_TRANSPORT: {current_transport}")
        else:
            st.caption("💡 提示：设置后点击保存，下次启动会自动加载。")
        
        https_proxy = st.text_input(
            "HTTPS_PROXY",
            value=current_https,
            help="例如：http://127.0.0.1:8001 或 socks5://127.0.0.1:7890",
        )
        http_proxy = st.text_input(
            "HTTP_PROXY",
            value=current_http,
        )
        all_proxy = st.text_input(
            "ALL_PROXY（可选）",
            value=current_all,
            help="SOCKS 代理常用此变量；如已设置 HTTPS_PROXY 可留空",
        )
        gemini_transport = st.selectbox(
            "GEMINI_TRANSPORT",
            options=["rest", "grpc"],
            index=0 if current_transport.lower() != "grpc" else 1,
            help="多数受限网络下建议用 rest",
        )
        if st.button("💾 保存为预设默认值", type="primary", key="save_proxy_settings"):
            # 保存到配置文件
            new_settings = {
                "https_proxy": https_proxy.strip(),
                "http_proxy": http_proxy.strip(),
                "all_proxy": all_proxy.strip(),
                "gemini_transport": gemini_transport.strip(),
                "gemini_api_key": user_settings.get("gemini_api_key", ""),  # 保留已有的 API Key
            }
            _save_user_settings(new_settings)
            
            # 同时应用到当前会话环境变量
            if https_proxy.strip():
                os.environ["HTTPS_PROXY"] = https_proxy.strip()
                os.environ["https_proxy"] = https_proxy.strip()
            else:
                os.environ.pop("HTTPS_PROXY", None)
                os.environ.pop("https_proxy", None)
            if http_proxy.strip():
                os.environ["HTTP_PROXY"] = http_proxy.strip()
                os.environ["http_proxy"] = http_proxy.strip()
            else:
                os.environ.pop("HTTP_PROXY", None)
                os.environ.pop("http_proxy", None)
            if all_proxy.strip():
                os.environ["ALL_PROXY"] = all_proxy.strip()
                os.environ["all_proxy"] = all_proxy.strip()
            else:
                os.environ.pop("ALL_PROXY", None)
                os.environ.pop("all_proxy", None)
            os.environ["GEMINI_TRANSPORT"] = gemini_transport.strip()
            st.success("✅ 已保存为预设默认值，下次启动会自动加载！")


def page_scout() -> None:
    st.subheader("🛸 侦察台（Scout）")
    st.caption("情报与素材管理中心：检索数据库、全网侦察、手动录入")
    
    from core.db_manager import DBManager
    db = DBManager()
    
    # 3 个 Tabs 布局
    tab1, tab2, tab3 = st.tabs(["🗄️ 素材库检索", "🛸 全网侦察", "📥 手动录入"])

    # =========================
    # Tab 1: 🗄️ 素材库检索 (Knowledge Base)
    # =========================
    with tab1:
        st.markdown("### 🗄️ 素材库检索（Knowledge Base）")
        st.caption("从数据库中检索笔记，支持关键词搜索和标签筛选。")
        
        # 筛选栏
        col1, col2 = st.columns([2, 1])
        with col1:
            search_keyword = st.text_input(
                "🔍 关键词搜索（标题/正文）",
                value=st.session_state.get("_kb_search_keyword", ""),
                help="在标题或正文中模糊匹配",
            )
        with col2:
            all_tags = db.get_all_tags()
            selected_tags = st.multiselect(
                "🏷️ 标签/分类筛选",
                options=all_tags if all_tags else [],
                default=st.session_state.get("_kb_selected_tags", []),
                help="可多选，笔记需包含任一标签",
            )
        
        st.session_state["_kb_search_keyword"] = search_keyword
        st.session_state["_kb_selected_tags"] = selected_tags
        
        # 执行搜索
        notes = []
        if search_keyword.strip() or selected_tags:
            try:
                notes = db.search_notes(
                    keyword=search_keyword.strip() if search_keyword.strip() else None,
                    tags=selected_tags if selected_tags else None,
                    limit=100,
                )
            except Exception as e:
                st.error(f"搜索失败：{e}")
                notes = []
        else:
            # 如果没有筛选条件，显示最近 50 条
            try:
                notes = db.search_notes(limit=50)
            except Exception as e:
                st.error(f"读取数据库失败：{e}")
                notes = []
        
        if not notes:
            st.info("暂无匹配的笔记。请尝试调整搜索条件，或前往其他 Tab 添加素材。")
        else:
            st.success(f"找到 {len(notes)} 条笔记")
            
            # 准备表格数据（隐藏 id 和 content）
            table_data = []
            for n in notes:
                table_data.append({
                    "select": False,  # st.data_editor 的勾选框列
                    "title": str(n.get("title") or "").strip()[:100] or "（无标题）",
                    "tags": str(n.get("tag_list") or "").strip()[:50] or "—",
                    "source": str(n.get("source_keyword") or "").strip()[:30] or "—",
                    "likes": int(n.get("likes") or 0),
                    "comments": int(n.get("comment_count") or 0),
                    "date": str(n.get("post_date") or "").strip()[:10] or "—",
                    "_id": str(n.get("id") or ""),  # 隐藏列，用于后续查找
                    "_content": str(n.get("content") or ""),  # 隐藏列，用于移交
                })
            
            # 使用 st.data_editor 展示表格
            edited_df = st.data_editor(
                table_data,
                column_config={
                    "select": st.column_config.CheckboxColumn("勾选", default=False),
                    "title": st.column_config.TextColumn("标题", width="large"),
                    "tags": st.column_config.TextColumn("标签", width="medium"),
                    "source": st.column_config.TextColumn("来源", width="small"),
                    "likes": st.column_config.NumberColumn("👍", width="small"),
                    "comments": st.column_config.NumberColumn("💬", width="small"),
                    "date": st.column_config.TextColumn("日期", width="small"),
                    "_id": st.column_config.TextColumn("ID", width="small", disabled=True),
                    "_content": st.column_config.TextColumn("内容", width="large", disabled=True),
                },
                hide_index=True,
                num_rows="dynamic",
                use_container_width=True,
            )
            
            # 提取勾选的行（st.data_editor 返回 DataFrame）
            try:
                import pandas as pd
                is_dataframe = isinstance(edited_df, pd.DataFrame)
            except ImportError:
                pd = None
                is_dataframe = False
            
            if is_dataframe:
                # 转换为字典列表
                edited_list = edited_df.to_dict("records")
            else:
                edited_list = edited_df if isinstance(edited_df, list) else []
            
            selected_rows = [r for r in edited_list if r.get("select", False)]
            
            if st.button("🚀 将勾选素材移交分析师（Send to Analyst）", type="primary"):
                if not selected_rows:
                    st.warning("请先勾选至少 1 条笔记")
                else:
                    # 构建 selected_materials
                    mats = []
                    for row in selected_rows:
                        mats.append({
                            "title": str(row.get("title", "")).strip(),
                            "url": f"https://www.xiaohongshu.com/explore/{row.get('_id', '')}" if row.get("_id") else "",
                            "content": str(row.get("_content", "")).strip(),
                        })
                    
                    st.session_state["selected_materials"] = mats
                    count = len(mats)
                    # 使用 st.toast（如果可用）或 st.success
                    if hasattr(st, "toast"):
                        st.toast(f"✅ {count} 篇笔记已装填！请前往 [分析师] 页面", icon="✅")
                    else:
                        st.success(f"✅ {count} 篇笔记已装填！请前往左侧 **🧠 分析（Analyst）** 页面继续。")
    
    # =========================
    # Tab 2: 🛸 全网侦察 (Live Scout)
    # =========================
    with tab2:
        st.markdown("### 🛸 全网侦察（Live Scout）")
        st.caption("支持两种侦察方式：🌐 外网搜索（DDG）或 📕 小红书爬虫（同步入库）。")
        
        # 模式切换
        scout_mode = st.radio(
            "🧭 侦察模式",
            options=["🌐 外网搜索（DDG）", "📕 小红书爬虫（同步入库）"],
            horizontal=True,
            index=st.session_state.get("_scout_mode_index", 0),
        )
        st.session_state["_scout_mode_index"] = 0 if scout_mode.startswith("🌐") else 1
        
        # =========================
        # 模式 A：小红书爬虫（同步）
        # =========================
        if scout_mode.startswith("📕"):
            st.markdown("### 📕 小红书爬虫（同步到数据库）")
            st.caption("点击运行后会抓取并写入 `database/redbook_core.db`，然后你可以在此页刷新并勾选导入素材。")

            kw = st.text_input("🔎 小红书关键词", value=st.session_state.get("_xhs_keyword", "送礼"))
            col_a, col_b, col_c, col_d = st.columns([1, 1, 1, 2])
            with col_a:
                run_crawler = st.button("🕷️ 运行爬虫（同步）", type="primary")
            with col_b:
                refresh = st.button("🔄 刷新列表")
            with col_c:
                stop_crawler = st.button("⛔ 停止爬虫")
            with col_d:
                limit = st.number_input("📌 展示数量", min_value=10, max_value=300, value=50, step=10)

            st.session_state["_xhs_keyword"] = kw

            job = st.session_state.get("_xhs_job")
            if isinstance(job, dict) and job.get("pid"):
                pid = int(job["pid"])
                running = True
                try:
                    os.kill(pid, 0)
                except Exception:
                    running = False
                job["running"] = running
                st.session_state["_xhs_job"] = job
            else:
                running = False

            if stop_crawler:
                job = st.session_state.get("_xhs_job") or {}
                pid = job.get("pid")
                if pid:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        st.warning("⛔ 已发送停止信号（SIGTERM），稍等几秒刷新状态。")
                    except Exception as e:
                        st.error(f"停止失败：{e}")
                else:
                    st.info("当前没有运行中的爬虫任务。")

            if run_crawler:
                if not kw.strip():
                    st.warning("请输入关键词")
                else:
                    job = st.session_state.get("_xhs_job")
                    if isinstance(job, dict) and job.get("running"):
                        st.warning("当前已有爬虫在运行，请先停止或等待完成。")
                    else:
                        start_count = db.count_notes_by_keyword(kw)
                        ts = int(time.time())
                        log_dir = PROJECT_ROOT / "output" / "logs"
                        log_dir.mkdir(parents=True, exist_ok=True)
                        log_path = str(log_dir / f"xhs_crawler_{kw}_{ts}.log")

                        cmd = [
                            sys.executable,
                            "-c",
                            (
                                "from skills.scout import ScoutSkill; "
                                f"ScoutSkill().run({kw!r})"
                            ),
                        ]
                        with open(log_path, "a", encoding="utf-8") as f:
                            proc = subprocess.Popen(
                                cmd,
                                cwd=str(PROJECT_ROOT),
                                stdout=f,
                                stderr=f,
                                env=os.environ.copy(),
                            )

                        st.session_state["_xhs_job"] = {
                            "pid": int(proc.pid),
                            "log_path": log_path,
                            "start_ts": float(time.time()),
                            "start_count": int(start_count),
                            "keyword": kw,
                            "running": True,
                        }
                        st.success("🕷️ 已启动爬虫后台任务！本页会自动显示进度与日志。")

            # 进度面板（轮询 DB + 日志）
            job = st.session_state.get("_xhs_job")
            if isinstance(job, dict) and job.get("keyword") == kw:
                total = db.count_notes_by_keyword(kw)
                start_count = int(job.get("start_count") or 0)
                delta = max(0, total - start_count)
                elapsed = int(time.time() - float(job.get("start_ts") or time.time()))
                st.markdown("### 📈 同步进度")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("📦 已入库（总）", total)
                c2.metric("🆕 本次新增", delta)
                c3.metric("⏱️ 运行时长(秒)", elapsed)
                c4.metric("🟢 运行中", "是" if job.get("running") else "否")

                latest = db.get_latest_note_by_keyword(kw)
                if latest:
                    st.caption(f"🧾 最新入库：{_safe_truncate(str(latest.get('title') or ''), 60)}  (id={latest.get('id')})")

                # 日志尾部
                with st.expander("📜 爬虫日志（尾部）", expanded=False):
                    log_path = str(job.get("log_path") or "")
                    if log_path and os.path.exists(log_path):
                        try:
                            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                                lines = f.readlines()[-120:]
                            st.text("".join(lines) if lines else "（日志为空）")
                        except Exception as e:
                            st.text(f"读取日志失败：{e}")
                    else:
                        st.text("（暂无日志文件）")

                # 自动刷新（仅在运行时）
                if job.get("running"):
                    st.caption("🔄 爬虫运行中：页面将每 5 秒自动刷新进度。")
                    st_autorefresh = getattr(st, "autorefresh", None)
                    if callable(st_autorefresh):
                        st_autorefresh(interval=5000, key="xhs_autorefresh")

            # 读取数据库展示
            try:
                order_by = st.selectbox("📊 排序", options=["likes", "comment_count", "post_date"], index=0)
                notes = db.get_notes_by_keyword(kw, limit=int(limit), order_by=order_by, descending=True)
            except Exception as e:
                notes = []
                st.error(f"读取数据库失败：{e}")

            if not notes:
                st.info("暂无入库结果。请先运行爬虫，或确认该关键词确实有数据写入数据库。")
            else:
                st.markdown("### ✅ 勾选要导入分析的笔记（来自数据库）")
                selected_ids: List[str] = []
                with st.form("xhs_select_form", clear_on_submit=False):
                    for i, n in enumerate(notes):
                        title = str(n.get("title") or "").strip() or "（无标题）"
                        likes = n.get("likes", 0)
                        comments = n.get("comment_count", 0)
                        date = n.get("post_date", "")
                        note_id = n.get("id", "")
                        checked = st.checkbox(f"{title}  👍{likes}  💬{comments}  🗓️{date}", key=f"xhs_cb_{i}")
                        if checked and note_id:
                            selected_ids.append(str(note_id))

                    import_btn = st.form_submit_button("📥 导入为素材（进入 Analyst）", type="primary")

                if import_btn:
                    if not selected_ids:
                        st.warning("请先勾选至少 1 条笔记")
                    else:
                        id_set = set(selected_ids)
                        mats = []
                        for n in notes:
                            if str(n.get("id")) in id_set:
                                mats.append(
                                    {
                                        "title": n.get("title") or "",
                                        "url": f"https://www.xiaohongshu.com/explore/{n.get('id')}",
                                        "content": n.get("content") or "",
                                    }
                                )
                        st.session_state["selected_materials"] = mats
                        st.success("✅ 已导入 selected_materials，请切换到左侧 **🧠 分析（Analyst）** 继续。")

                # CSV 下载
                st.markdown("### ⬇️ CSV 导出")
                export_scope = st.radio("导出范围", options=["导出当前关键词全部（按展示上限）", "仅导出勾选项"], horizontal=True)
                export_rows: List[Dict[str, Any]] = []
                if export_scope.startswith("仅"):
                    id_set = set(selected_ids)
                    export_rows = [n for n in notes if str(n.get("id")) in id_set]
                else:
                    export_rows = notes

                if export_rows:
                    output = io.StringIO()
                    writer = csv.DictWriter(
                        output,
                        fieldnames=["id", "title", "content", "likes", "comment_count", "post_date", "source_keyword", "tag_list", "processed"],
                        extrasaction="ignore",
                    )
                    writer.writeheader()
                    for r in export_rows:
                        writer.writerow(r)
                    csv_bytes = output.getvalue().encode("utf-8-sig")
                    st.download_button(
                        "💾 下载 CSV",
                        data=csv_bytes,
                        file_name=f"xhs_{kw}_notes.csv",
                        mime="text/csv",
                    )
                else:
                    st.caption("暂无可导出的数据（先刷新/勾选）。")
        
        # =========================
        # 模式 B：外网搜索（DDG）
        # =========================
        else:
            st.markdown("### 🌐 外网搜索（DuckDuckGo）")
            st.caption("使用 DuckDuckGo 搜索外部资料，支持深度挖掘并存入素材库。")
        
            # 保留原有的 DDG 搜索功能
        col1, col2 = st.columns([3, 1])
        with col1:
            query = st.text_input("🔎 关键词", value=st.session_state.get("_scout_query", ""))
        with col2:
            max_results = st.number_input("📌 结果数量", min_value=3, max_value=30, value=10, step=1)

        if st.button("🚀 开始搜索", type="primary"):
            st.session_state["_scout_query"] = query
            st.session_state["_scout_searched"] = True
            st.session_state["_scout_error"] = ""
            with st.spinner("🛰️ 正在搜索…"):
                try:
                    st.session_state["scout_results"] = ddg_search(query, int(max_results))
                except Exception as e:
                    st.session_state["scout_results"] = []
                    st.session_state["_scout_error"] = str(e)

        results: List[Dict[str, str]] = st.session_state.get("scout_results", [])
        if not results:
            err = (st.session_state.get("_scout_error") or "").strip()
            if err:
                st.error(f"❌ 搜索失败：{err}")
                st.caption("💡 如处于受限网络环境，请确认代理/网络可用，或安装 `duckduckgo_search` 以提升稳定性。")
            elif st.session_state.get("_scout_searched"):
                st.warning("未搜到结果（也可能是网络受限导致返回为空），请换关键词再试。")
                st.caption("💡 建议：安装 `duckduckgo_search`（更稳），或确认当前网络/代理可正常访问外网。")
            else:
                st.info("👈 请输入关键词并点击 **开始搜索**")
        else:
            st.markdown("### 📋 搜索结果（勾选你要深挖的链接）")

            selected_urls: List[str] = []
            with st.form("scout_select_form", clear_on_submit=False):
                for i, r in enumerate(results):
                    title = r.get("title") or "（无标题）"
                    url = r.get("url") or ""
                    snippet = r.get("snippet") or ""

                    checked = st.checkbox(f"{title}", key=f"scout_cb_{i}")
                    st.caption(url)
                    if snippet:
                        st.caption(_safe_truncate(snippet, 240))
                    if checked and url:
                        selected_urls.append(url)

                deep_dive = st.form_submit_button("🧲 深度挖掘（Jina Reader）", type="primary")

            if deep_dive:
                if not selected_urls:
                    st.warning("请先勾选至少 1 条结果")
                else:
                    materials: List[Dict[str, Any]] = []
                    with st.spinner("🧾 正在抓取正文（可能需要几十秒）…"):
                        for url in selected_urls:
                            try:
                                fulltext = fetch_fulltext_via_jina(url)
                                title = ""
                                for r in results:
                                    if (r.get("url") or "").strip() == url.strip():
                                        title = r.get("title") or ""
                                        break
                                materials.append({"title": title, "url": url, "content": fulltext})
                            except Exception as e:
                                materials.append({"title": "", "url": url, "content": f"（抓取失败）{e}"})

                    # 提供两个选项：直接移交分析师 或 存入素材库
                    col_save, col_send = st.columns(2)
                    with col_save:
                        if st.button("💾 存入素材库", type="secondary"):
                            saved_count = 0
                            for m in materials:
                                if m.get("content") and not m.get("content", "").startswith("（抓取失败）"):
                                    try:
                                        db.add_note_manual(
                                            title=m.get("title", "").strip() or "（无标题）",
                                            content=m.get("content", "").strip(),
                                            source="DDG搜索",
                                            tags=["外网搜索", "DDG"],
                                        )
                                        saved_count += 1
                                    except Exception as e:
                                        st.error(f"保存失败：{e}")
                            if saved_count > 0:
                                st.success(f"✅ 已存入 {saved_count} 条笔记到素材库")
                    with col_send:
                        if st.button("🚀 直接移交分析师", type="primary"):
                            st.session_state["selected_materials"] = materials
                            if hasattr(st, "toast"):
                                st.toast(f"✅ {len(materials)} 篇素材已装填！请前往 [分析师] 页面", icon="✅")
                            else:
                                st.success(f"✅ {len(materials)} 篇素材已装填！请前往左侧 **🧠 分析（Analyst）** 页面继续。")
    
    # =========================
    # Tab 3: 📥 手动录入 (Manual Import)
    # =========================
    with tab3:
        st.markdown("### 📥 手动录入（Manual Import）")
        st.caption("手动添加笔记到素材库，支持标题、正文、标签。")
        
        with st.form("manual_import_form", clear_on_submit=True):
            title = st.text_input("笔记标题", value="", placeholder="例如：小红书爆款标题公式")
            content = st.text_area("笔记正文/干货内容", height=300, placeholder="请输入完整的笔记内容...")
            tags_input = st.text_input(
                "标签（逗号分隔）",
                value="手动录入, 竞品",
                help="例如：手动录入, 竞品, 标题公式",
            )
            comment_input = st.text_area(
                "🗣️ 精选评论 (用于挖掘痛点)",
                value="",
                height=150,
                placeholder="请输入精选评论原文（多条评论可用换行分隔）...",
                help="用于 VOC 分析，挖掘用户痛点和选题方向",
            )
            
            submit = st.form_submit_button("💾 确认入库", type="primary")
            
            if submit:
                if not title.strip():
                    st.warning("请输入笔记标题")
                elif not content.strip():
                    st.warning("请输入笔记正文")
                else:
                    tags_list = [t.strip() for t in tags_input.split(",") if t.strip()] if tags_input.strip() else ["手动录入"]
                    try:
                        success = db.add_note_manual(
                            title=title.strip(),
                            content=content.strip(),
                            source="Manual",
                            tags=tags_list,
                            raw_comments=comment_input.strip() if comment_input.strip() else None,
                        )
                        if success:
                            st.success("✅ 笔记已成功入库！可在 **🗄️ 素材库检索** Tab 中查看。")
                        else:
                            st.warning("⚠️ 入库失败（可能是 ID 冲突）")
                    except Exception as e:
                        st.error(f"入库失败：{e}")

    # =========================
    # 已导入素材预览（所有 Tab 共享）
    # =========================
    with st.expander("🧾 已导入素材预览", expanded=False):
        mats: List[Dict[str, Any]] = st.session_state.get("selected_materials", [])
        if not mats:
            st.caption("暂无")
        else:
            for idx, m in enumerate(mats, start=1):
                st.markdown(f"**素材 {idx}**：{m.get('title') or '（无标题）'}")
                st.caption(m.get("url") or "")
                st.text(_safe_truncate(str(m.get("content") or ""), 1200))

    # =========================
    # 旧代码已移除，功能已整合到上述 3 个 Tabs
    # =========================


def page_analyst() -> None:
    st.subheader("🧠 分析台（Analyst）")
    st.caption("核心升级：对每一篇笔记独立拆解，分析结果回写数据库，并自动完善标签体系。")

    from core.db_manager import DBManager
    db = DBManager()

    # ===== 数据源：待分析笔记 =====
    with st.expander("🗂️ 待分析笔记列表（来自数据库）", expanded=True):
        # 筛选栏
        col1, col2, col3 = st.columns([2, 2, 1])
        
        with col1:
            # 搜索关键词筛选
            all_keywords = db.get_all_search_keywords()
            keyword_options = ["全部"] + (all_keywords if all_keywords else [])
            selected_keyword_idx = st.session_state.get("_analyst_keyword_filter_idx", 0)
            if selected_keyword_idx >= len(keyword_options):
                selected_keyword_idx = 0
            
            selected_keyword = st.selectbox(
                "🔍 搜索关键词筛选",
                options=keyword_options,
                index=selected_keyword_idx,
                key="analyst_keyword_filter",
            )
            st.session_state["_analyst_keyword_filter_idx"] = keyword_options.index(selected_keyword) if selected_keyword in keyword_options else 0
        
        with col2:
            limit = st.number_input("📌 载入条数", min_value=10, max_value=300, value=80, step=10)
        
        with col3:
            st.write("")  # 占位
            st.write("")  # 占位
        
        # 根据筛选条件获取笔记（只显示未分析的）
        filter_keyword = None if selected_keyword == "全部" else selected_keyword
        pending = db.get_notes_pending_analysis(limit=int(limit), search_keyword=filter_keyword)
        
        if not pending:
            st.info("暂无待分析笔记（数据库里都已分析完成，或当前筛选条件下无结果）。")
            pending = []

        # 全选/取消全选功能
        selected_ids: List[str] = st.session_state.get("_analyst_selected_ids", [])
        selected_set = set(selected_ids)
        
        col_select_all, col_info = st.columns([1, 4])
        with col_select_all:
            if st.button("✅ 全选", key="analyst_select_all"):
                selected_set = {str(n.get("id") or "") for n in pending}
                st.session_state["_analyst_selected_ids"] = sorted(list(selected_set))
                st.rerun()
            if st.button("❌ 取消全选", key="analyst_deselect_all"):
                selected_set = set()
                st.session_state["_analyst_selected_ids"] = []
                st.rerun()
        
        with col_info:
            if selected_set:
                st.info(f"已选中 {len(selected_set)} 篇笔记")

        # 笔记列表（复选框不折叠，直接显示）
        for n in pending:
            nid = str(n.get("id") or "")
            title = str(n.get("title") or "（无标题）").strip()
            kw = (n.get("search_keyword") or n.get("source_keyword") or "").strip()
            likes = int(n.get("likes") or 0)
            comment_count = int(n.get("comment_count") or 0)
            tag_list = str(n.get("tag_list") or "").strip()
            
            # 复选框直接显示，不放在 expander 内
            checked = st.checkbox(
                f"🟡 {title}  ｜ 👍{likes}  ｜ 🔑{kw or '—'}",
                value=(nid in selected_set),
                key=f"an_sel_{nid}",
            )
            if checked:
                selected_set.add(nid)
            else:
                selected_set.discard(nid)
            
            # 详细信息放在可折叠的 expander 中
            with st.expander(f"📄 查看详情：{_safe_truncate(title, 50)}", expanded=False):
                # 基本信息区域
                info_col1, info_col2, info_col3 = st.columns(3)
                with info_col1:
                    st.caption(f"🔑 关键词：{kw or '—'}")
                with info_col2:
                    st.caption(f"👍 点赞：{likes}")
                with info_col3:
                    st.caption(f"💬 评论：{comment_count}")
                
                if tag_list:
                    st.caption(f"🏷️ 标签：{tag_list}")
                
                st.divider()
                
                # 🗣️ 评论分析区域（独立展示，更突出）
                raw_comments = str(n.get("raw_comments") or "").strip()
                comments_insight = str(n.get("comments_insight") or "").strip()
                
                if raw_comments:
                    # 评论统计信息
                    comment_lines = raw_comments.split("\n")
                    comment_count_actual = len([line for line in comment_lines if line.strip()])
                    comment_chars = len(raw_comments)
                    
                    # 评论区域标题和统计
                    comment_header_col1, comment_header_col2 = st.columns([3, 1])
                    with comment_header_col1:
                        st.markdown("#### 🗣️ 评论区分析")
                    with comment_header_col2:
                        status_badge = "✅ 已分析" if comments_insight else "⏳ 待分析"
                        st.caption(status_badge)
                    
                    # 评论统计卡片
                    stat_col1, stat_col2, stat_col3 = st.columns(3)
                    with stat_col1:
                        st.metric("评论条数", comment_count_actual)
                    with stat_col2:
                        st.metric("总字符数", f"{comment_chars:,}")
                    with stat_col3:
                        avg_chars = comment_chars // comment_count_actual if comment_count_actual > 0 else 0
                        st.metric("平均长度", f"{avg_chars}")
                    
                    # 评论内容预览（可展开）
                    with st.expander(f"📝 查看评论内容（{comment_count_actual} 条）", expanded=False):
                        # 显示评论列表，每条评论单独显示
                        comment_list = [line.strip() for line in comment_lines if line.strip()]
                        for idx, comment in enumerate(comment_list[:50], 1):  # 最多显示50条
                            st.markdown(f"**{idx}.** {comment}")
                        if len(comment_list) > 50:
                            st.caption(f"... 还有 {len(comment_list) - 50} 条评论未显示")
                    
                    st.divider()
                    
                    # 分析结果区域
                    if comments_insight:
                        st.markdown("#### 💡 VOC 洞察报告")
                        # 使用容器突出显示分析结果
                        with st.container():
                            st.markdown(comments_insight)
                        
                        # 重新分析按钮
                        reanalyze_key = f"reanalyze_comments_{nid}"
                        if st.button("🔄 重新分析", key=reanalyze_key, type="secondary", use_container_width=True):
                            try:
                                from skills.analyst import AnalystSkill
                                
                                with st.spinner("🧠 正在重新分析评论痛点（可能需要 10-30 秒）..."):
                                    analyst = AnalystSkill(db=db)
                                    insight = analyst.analyze_comments(raw_comments)
                                
                                # 保存到数据库
                                db.update_note(nid, {"comments_insight": insight})
                                
                                st.success("✅ 评论分析完成！")
                                st.rerun()
                            except Exception as e:
                                st.error(f"分析失败：{e}")
                    else:
                        # 未分析状态：显示分析按钮
                        analyze_key = f"analyze_comments_{nid}"
                        st.info("💡 点击下方按钮开始分析评论区痛点，挖掘用户真实需求")
                        if st.button("🗣️ 开始分析评论区痛点", key=analyze_key, type="primary", use_container_width=True):
                            try:
                                from skills.analyst import AnalystSkill
                                
                                with st.spinner("🧠 正在分析评论痛点（可能需要 10-30 秒）..."):
                                    analyst = AnalystSkill(db=db)
                                    insight = analyst.analyze_comments(raw_comments)
                                
                                # 保存到数据库
                                db.update_note(nid, {"comments_insight": insight})
                                
                                st.success("✅ 评论分析完成！")
                                st.rerun()
                            except Exception as e:
                                st.error(f"分析失败：{e}")
                    
                    st.divider()
                else:
                    # 没有评论的情况
                    st.info("ℹ️ 该笔记暂无评论数据")
                
                # 正文内容预览
                with st.expander("📄 正文内容（预览）", expanded=False):
                    content = str(n.get("content") or "").strip()
                    st.text(_safe_truncate(content, 2000))

        st.session_state["_analyst_selected_ids"] = sorted(list(selected_set))

    selected_ids = st.session_state.get("_analyst_selected_ids", []) or []
    st.markdown("### 🚀 批量分析")
    st.caption("会逐篇调用 Gemini 分析，并将 analysis_result / ai_tags / tag_list 回写到数据库。")

    if st.button(f"🚀 开始批量分析（选中 {len(selected_ids)} 篇）", type="primary", disabled=(len(selected_ids) == 0)):
        try:
            from skills.analyst import AnalystSkill

            # 初始化 AnalystSkill（可能因网络预检失败，但允许继续尝试）
            try:
                analyst = AnalystSkill(db=db)
            except Exception as init_err:
                err_msg = str(init_err)
                if "网络预检失败" in err_msg or "无法连接 Gemini" in err_msg or "timed out" in err_msg.lower():
                    st.error(
                        f"❌ 网络连接失败：{err_msg}\n\n"
                        "💡 **解决方案**：\n"
                        "1. 请在左侧侧边栏找到 **🌐 网络/代理（可选）** 展开项\n"
                        "2. 填入你的代理地址（如：`http://127.0.0.1:7890` 或 `socks5://127.0.0.1:7890`）\n"
                        "3. 点击 **✅ 应用代理设置**\n"
                        "4. 然后重新点击 **🚀 开始批量分析** 按钮"
                    )
                    st.stop()
                else:
                    raise  # 其他错误直接抛出
            progress = st.progress(0)
            total = len(selected_ids)
            
            # 实时日志显示区域
            log_container = st.empty()
            result_area = st.container()

            # 批量拉取被选中的笔记（含正文 + search_keyword）
            id_to_note = {str(n.get("id") or ""): n for n in db.get_notes_by_ids(selected_ids)}

            for idx, nid in enumerate(selected_ids, start=1):
                note = id_to_note.get(str(nid)) or {}
                content = str(note.get("content") or "").strip()
                kw = (note.get("search_keyword") or note.get("source_keyword") or "").strip()
                title = str(note.get("title") or nid).strip()

                # 实时更新日志
                with log_container:
                    st.info(f"🔄 [{idx}/{total}] 正在分析：{_safe_truncate(title, 60)}")
                    st.caption(f"⏳ 调用 Gemini API 中...（通常需要 10-60 秒，请耐心等待）")

                try:
                    # 调用分析（可能耗时较长）
                    with st.spinner(f"分析中：{_safe_truncate(title, 40)}..."):
                        out = analyst.analyze_single_note(content, original_keyword=kw or None)
                    
                    report_md = out.get("report") or ""
                    ai_tags = out.get("tags") or ""

                    # 写入数据库
                    db.update_note_analysis(str(nid), report_md, ai_tags)

                    # 更新日志和结果
                    with log_container:
                        st.success(f"✅ [{idx}/{total}] 已完成：{_safe_truncate(title, 60)}")
                    
                    with result_area:
                        st.markdown(f"#### ✅ {idx}/{total} - {_safe_truncate(title, 80)}")
                        st.caption(f"🏷️ 新标签：{ai_tags}")
                        with st.expander("📄 分析报告（展开查看）", expanded=False):
                            st.markdown(report_md or "（空）")

                except Exception as e:
                    error_msg = str(e)
                    with log_container:
                        st.error(f"❌ [{idx}/{total}] 分析失败：{_safe_truncate(title, 60)}")
                        st.caption(f"错误：{_safe_truncate(error_msg, 200)}")
                    with result_area:
                        st.error(f"❌ {idx}/{total} - {_safe_truncate(title, 80)}")
                        st.caption(f"错误：{error_msg}")
                    # 继续处理下一篇，不中断整个流程
                    continue

                # 更新进度条
                progress.progress(int(idx / total * 100))

            if hasattr(st, "toast"):
                st.toast(f"✅ 批量分析完成：共 {total} 篇", icon="✅")
            st.success(f"✅ 批量分析完成：共 {total} 篇。")
            
            # 批量分析完成后，提供生成策略报告的选项
            st.markdown("---")
            st.markdown("### 📊 生成策略报告（用于写作阶段）")
            st.caption("基于已分析的笔记生成策略报告，供 ✍️ 写作台使用。")
            
            # 从数据库读取刚才分析的笔记（包含 analysis_result）
            analyzed_notes = db.get_notes_by_ids(selected_ids)
            analyzed_with_results = [n for n in analyzed_notes if (n.get("analysis_result") or "").strip()]
            
            if analyzed_with_results:
                st.info(f"📌 找到 {len(analyzed_with_results)} 篇已分析的笔记，可用于生成策略报告。")
                
                topic_hint = st.text_input(
                    "🎯 主题提示（可选）",
                    value=st.session_state.get("_topic_hint", ""),
                    help="例如：职场干货、Python入门、情绪价值等",
                )
                st.session_state["_topic_hint"] = topic_hint
                
                if st.button("🧠 生成策略报告", type="primary"):
                    try:
                        from skills.analyst import AnalystSkill
                        # 重新初始化 AnalystSkill（确保使用最新配置）
                        analyst = AnalystSkill(db=db)
                        
                        # 构建 materials 格式（用于 generate_strategy_report）
                        materials = []
                        for n in analyzed_with_results:
                            materials.append({
                                "title": str(n.get("title") or "").strip() or "（无标题）",
                                "url": f"https://www.xiaohongshu.com/explore/{n.get('id', '')}" if n.get("id") else "",
                                "content": str(n.get("content") or "").strip() or str(n.get("analysis_result") or "").strip(),
                            })
                        
                        with st.spinner("🧠 正在生成策略报告（可能需要 30-90 秒）…"):
                            report = analyst.generate_strategy_report(materials, topic_hint=topic_hint)
                            st.session_state["strategy_report"] = report
                        
                        st.success("✅ 策略报告生成完成！请切换到左侧 **✍️ 写作（Writer）** 继续。")
                        with st.expander("📄 策略报告预览", expanded=True):
                            st.markdown(report)
                    except Exception as e:
                        st.error(f"生成策略报告失败：{e}")
            else:
                st.warning("⚠️ 没有找到已分析的笔记。请确保批量分析已完成并成功写入数据库。")

        except Exception as e:
            st.error(f"批量分析失败：{e}")

    st.markdown("---")
    with st.expander("ℹ️ 说明：标签合并规则（强制）", expanded=False):
        st.markdown(
            "- **Final Tags = [Original Search Keyword] + [Manual Tags] + [AI Generated Tags]**\n"
            "- 其中 **搜索词永远是第一权重标签**，便于后续分类管理。\n"
            "- 以上规则由 `core/db_manager.py::update_note_analysis()` 强制执行。"
        )


def page_writer() -> None:
    st.subheader("✍️ 写作台（Writer）")
    st.caption("从数据库选择已分析的笔记作为参考，用于生成文案")

    from core.db_manager import DBManager
    db = DBManager()

    # ===== 从数据库读取已分析的笔记 =====
    st.markdown("### 📚 选择已分析的笔记作为参考")
    
    # 筛选栏
    col_filter1, col_filter2, col_filter3, col_limit = st.columns([2, 2, 2, 1])
    
    with col_filter1:
        # 搜索关键词筛选
        all_keywords = db.get_all_search_keywords()
        keyword_options = ["全部"] + (all_keywords if all_keywords else [])
        selected_keyword_idx = st.session_state.get("_writer_keyword_filter_idx", 0)
        if selected_keyword_idx >= len(keyword_options):
            selected_keyword_idx = 0
        
        filter_keyword = st.selectbox(
            "🔍 搜索关键词",
            options=keyword_options,
            index=selected_keyword_idx,
            key="writer_keyword_filter",
            help="按搜索关键词筛选",
        )
        st.session_state["_writer_keyword_filter_idx"] = keyword_options.index(filter_keyword) if filter_keyword in keyword_options else 0
    
    with col_filter2:
        # 标题关键词筛选
        filter_title = st.text_input(
            "📝 标题关键词",
            value=st.session_state.get("_writer_title_filter", ""),
            key="writer_title_filter",
            help="在标题中搜索包含此关键词的笔记",
            placeholder="输入标题关键词...",
        )
        st.session_state["_writer_title_filter"] = filter_title
    
    with col_filter3:
        # 标签筛选
        all_tags = db.get_all_tags()
        tag_options = ["全部"] + (all_tags if all_tags else [])
        selected_tag_idx = st.session_state.get("_writer_tag_filter_idx", 0)
        if selected_tag_idx >= len(tag_options):
            selected_tag_idx = 0
        
        filter_tag = st.selectbox(
            "🏷️ 标签筛选",
            options=tag_options,
            index=selected_tag_idx,
            key="writer_tag_filter",
            help="按标签筛选笔记",
        )
        st.session_state["_writer_tag_filter_idx"] = tag_options.index(filter_tag) if filter_tag in tag_options else 0
    
    with col_limit:
        limit_notes = st.number_input("📌 载入条数", min_value=1, max_value=50, value=10, step=5)
    
    # 根据筛选条件查询笔记
    try:
        # 构建筛选条件
        search_keyword = None if filter_keyword == "全部" else filter_keyword
        search_title = filter_title.strip() if filter_title.strip() else None
        search_tags = [filter_tag] if filter_tag != "全部" and filter_tag.strip() else None
        
        # 使用 search_notes 方法进行筛选
        all_analyzed = db.search_notes(
            keyword=search_title,  # 标题关键词搜索
            tags=search_tags,      # 标签筛选
            limit=int(limit_notes)
        )
        
        # 进一步筛选：只保留有分析结果的笔记
        analyzed_notes = [n for n in all_analyzed if (n.get("analysis_result") or "").strip()]
        
        # 如果指定了搜索关键词，进一步筛选
        if search_keyword:
            analyzed_notes = [
                n for n in analyzed_notes 
                if (n.get("search_keyword") or n.get("source_keyword") or "").strip() == search_keyword
            ]
    except Exception as e:
        analyzed_notes = []
        st.error(f"查询失败：{e}")
    
    if not analyzed_notes:
        st.warning("⚠️ 暂无已分析的笔记（或当前筛选条件下无结果）。请先在 🧠 分析台进行批量分析。")
        reference_content = ""
        analyzed_notes_context = []
    else:
        st.success(f"✅ 找到 {len(analyzed_notes)} 篇已分析的笔记")
        
        # 选择器
        selected_note_ids: List[str] = st.session_state.get("_writer_selected_note_ids", [])
        selected_set = set(selected_note_ids)
        
        for n in analyzed_notes[:50]:  # 最多显示 50 条
            nid = str(n.get("id") or "")
            title = str(n.get("title") or "（无标题）").strip()
            kw = (n.get("search_keyword") or n.get("source_keyword") or "").strip()
            analysis = str(n.get("analysis_result") or "").strip()
            tags = str(n.get("tag_list") or "").strip()
            
            checked = st.checkbox(
                f"{title} | 🔑{kw or '—'} | 🏷️{tags[:30] if tags else '—'} | 📊 分析长度：{len(analysis)} 字符",
                value=(nid in selected_set),
                key=f"writer_note_{nid}",
            )
            if checked:
                selected_set.add(nid)
            else:
                selected_set.discard(nid)
        
        st.session_state["_writer_selected_note_ids"] = sorted(list(selected_set))
        selected_note_ids = st.session_state.get("_writer_selected_note_ids", []) or []
        
        if selected_note_ids:
            # 获取选中的笔记
            selected_notes = db.get_notes_by_ids(selected_note_ids)
            analyzed_notes_context = selected_notes
            
            # 构建参考内容（汇总所有分析结果）
            context_parts = []
            for idx, n in enumerate(selected_notes, start=1):
                title = str(n.get("title") or "").strip() or "（无标题）"
                analysis = str(n.get("analysis_result") or "").strip()
                tags = str(n.get("tag_list") or "").strip()
                
                context_parts.append(f"--- 参考笔记 {idx}：{title} ---")
                if tags:
                    context_parts.append(f"标签：{tags}")
                if analysis:
                    context_parts.append(f"分析报告：\n{analysis}")
                context_parts.append("")  # 空行分隔
            
            reference_content = "\n".join(context_parts)
            st.info(f"✅ 已选择 {len(selected_notes)} 篇笔记作为参考（总分析内容：{len(reference_content)} 字符）")
        else:
            st.caption("💡 请勾选至少 1 篇已分析的笔记作为参考")
            reference_content = ""
            analyzed_notes_context = []

    # 如果没有参考内容，显示提示但不阻止继续
    if not reference_content and not analyzed_notes_context:
        st.warning("⚠️ 当前没有可用的参考内容。建议：")
        st.markdown("""
        1. 在 🧠 分析台进行批量分析
        2. 使用上方的筛选条件查找已分析的笔记
        3. 勾选笔记作为参考
        """)
        # 不 return，允许用户手动输入 title_formula 和 structure_logic

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 🤖 AI 草稿")

        persona = st.selectbox(
            "🎭 选择人设",
            options=["sales_expert", "executive_assistant", "old_xu"],
            index=0,
            help="对应 personas 目录下的 system_prompt 与知识库",
        )

        # ===== 知识库管理 =====
        with st.expander("📚 知识库管理（上传文件）", expanded=False):
            from pathlib import Path
            project_root = Path(__file__).resolve().parent
            persona_dir_map = {
                "sales_expert": "sales_expert",
                "executive_assistant": "executive_assistant",
                "old_xu": "sales_expert",
            }
            persona_dir = persona_dir_map.get(persona, "sales_expert")
            knowledge_dir = project_root / "personas" / persona_dir / "knowledge"
            knowledge_dir.mkdir(parents=True, exist_ok=True)
            
            st.caption(f"当前人设：{persona} → 知识库路径：`personas/{persona_dir}/knowledge/`")
            
            # 显示现有知识库文件
            existing_files = list(knowledge_dir.glob("*.md")) + list(knowledge_dir.glob("*.txt"))
            if existing_files:
                st.markdown("**现有知识库文件：**")
                for f in sorted(existing_files):
                    col_file, col_del = st.columns([4, 1])
                    with col_file:
                        st.text(f"📄 {f.name} ({f.stat().st_size} 字节)")
                    with col_del:
                        if st.button("🗑️", key=f"del_{f.name}", help="删除文件"):
                            try:
                                f.unlink()
                                st.success(f"✅ 已删除 {f.name}")
                                st.rerun()
                            except Exception as e:
                                st.error(f"删除失败：{e}")
            else:
                st.info("暂无知识库文件")
            
            # 文件上传
            uploaded_file = st.file_uploader(
                "📤 上传知识库文件（.md 或 .txt）",
                type=["md", "txt"],
                key=f"upload_kb_{persona}",
            )
            if uploaded_file is not None:
                file_path = knowledge_dir / uploaded_file.name
                try:
                    with open(file_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    st.success(f"✅ 已保存：{uploaded_file.name}")
                    st.rerun()
                except Exception as e:
                    st.error(f"保存失败：{e}")

        # ===== 标题选择（从数据库） =====
        st.markdown("#### 📝 标题参考")
        
        # 筛选栏
        col_title_filter1, col_title_filter2 = st.columns([1, 1])
        
        with col_title_filter1:
            # 搜索关键词筛选
            all_keywords = db.get_all_search_keywords()
            title_keyword_options = ["全部"] + (all_keywords if all_keywords else [])
            selected_title_keyword_idx = st.session_state.get("_title_keyword_filter_idx", 0)
            if selected_title_keyword_idx >= len(title_keyword_options):
                selected_title_keyword_idx = 0
            
            title_filter_keyword = st.selectbox(
                "🔍 关键词筛选",
                options=title_keyword_options,
                index=selected_title_keyword_idx,
                key="title_keyword_filter",
                help="按搜索关键词筛选标题",
            )
            st.session_state["_title_keyword_filter_idx"] = title_keyword_options.index(title_filter_keyword) if title_filter_keyword in title_keyword_options else 0
        
        with col_title_filter2:
            # 标签筛选
            all_tags = db.get_all_tags()
            title_tag_options = ["全部"] + (all_tags if all_tags else [])
            selected_title_tag_idx = st.session_state.get("_title_tag_filter_idx", 0)
            if selected_title_tag_idx >= len(title_tag_options):
                selected_title_tag_idx = 0
            
            title_filter_tag = st.selectbox(
                "🏷️ 标签筛选",
                options=title_tag_options,
                index=selected_title_tag_idx,
                key="title_tag_filter",
                help="按标签筛选标题",
            )
            st.session_state["_title_tag_filter_idx"] = title_tag_options.index(title_filter_tag) if title_filter_tag in title_tag_options else 0
        
        # 根据筛选条件从数据库获取标题（从原始爬虫数据中查询）
        try:
            # 构建筛选条件
            search_keyword = None if title_filter_keyword == "全部" else title_filter_keyword
            search_tags = [title_filter_tag] if title_filter_tag != "全部" and title_filter_tag.strip() else None
            
            # 直接从数据库查询原始爬虫数据（不要求有分析结果）
            with sqlite3.connect(db.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # 构建 SQL 查询条件
                conditions = []  # 不限制 analysis_result，从所有爬虫数据中查询
                params = []
                
                # 搜索关键词筛选（search_keyword 或 source_keyword）
                if search_keyword:
                    conditions.append("(COALESCE(NULLIF(TRIM(search_keyword), ''), source_keyword) = ?)")
                    params.append(search_keyword)
                
                # 标签筛选
                if search_tags and isinstance(search_tags, list) and len(search_tags) > 0:
                    tag_conditions = []
                    for tag in search_tags:
                        if tag and tag.strip():
                            tag_conditions.append("tag_list LIKE ?")
                            params.append(f"%{tag.strip()}%")
                    if tag_conditions:
                        conditions.append(f"({' OR '.join(tag_conditions)})")
                
                # 确保标题不为空
                conditions.append("(title IS NOT NULL AND TRIM(title) != '')")
                
                where_clause = " AND ".join(conditions) if conditions else "1=1"
                
                # 执行查询
                query = f"""
                    SELECT DISTINCT title, search_keyword, source_keyword, tag_list
                    FROM scraped_notes
                    WHERE {where_clause}
                    ORDER BY post_date DESC, rowid DESC
                    LIMIT 100
                """
                
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                # 提取标题并去重
                available_titles = []
                seen = set()
                for row in rows:
                    title = str(row["title"] or "").strip()
                    if title and title not in seen:
                        seen.add(title)
                        available_titles.append(title)
                        
        except Exception as e:
            available_titles = []
            st.caption(f"⚠️ 数据库查询失败：{e}")
        
        if available_titles:
            st.caption(f"📌 找到 {len(available_titles)} 个可用标题（已按筛选条件过滤）")
            
            selected_title_idx = st.session_state.get("_selected_title_idx", 0)
            if selected_title_idx >= len(available_titles):
                selected_title_idx = 0
            
            selected_title = st.selectbox(
                "📌 从数据库选择标题参考",
                options=available_titles,
                index=selected_title_idx,
                key="title_selector",
                help="从已分析的笔记中选择标题作为参考（已按关键词和标签筛选）",
            )
            st.session_state["_selected_title_idx"] = available_titles.index(selected_title) if selected_title in available_titles else 0
            
            # 允许修改选中的标题
            title_formula = st.text_area(
                "✏️ 标题公式（可修改）",
                value=st.session_state.get("_title_formula", selected_title),
                height=80,
                help="基于选中的标题进行修改，或直接输入新的标题公式",
            )
            st.session_state["_title_formula"] = title_formula
        else:
            st.warning("⚠️ 暂无可用标题参考（当前筛选条件下无结果）")
            title_formula = st.text_input(
                "🧪 标题公式（手动输入）",
                value=st.session_state.get("_title_formula", ""),
                help="暂无可用标题参考，请手动输入或调整筛选条件",
            )
            st.session_state["_title_formula"] = title_formula

        # ===== 结构逻辑（人工审核反馈） =====
        st.markdown("#### 🧱 结构逻辑（人工审核反馈）")
        structure_logic = st.text_area(
            "📝 结构逻辑/审核反馈",
            value=st.session_state.get("_structure_logic", ""),
            height=120,
            help="输入结构逻辑要求或人工审核反馈意见，这些反馈会被保存到记忆库中",
            placeholder="例如：开头要抓人、中间要有痛点、结尾要有行动号召...",
        )
        st.session_state["_structure_logic"] = structure_logic
        
        # 如果输入了结构逻辑，提示会保存到记忆库
        if structure_logic.strip():
            st.caption("💡 此反馈将在生成文案后自动保存到记忆库，用于后续生成时参考")

        # 显示参考内容预览
        if reference_content:
            with st.expander("📄 查看参考内容", expanded=False):
                st.markdown("**已分析的笔记汇总：**")
                st.markdown(reference_content[:2000] + "..." if len(reference_content) > 2000 else reference_content)
                st.caption(f"（总长度：{len(reference_content)} 字符，已截断预览）")
        else:
            st.caption("💡 暂无参考内容，将使用你手动输入的标题公式和结构逻辑")

        if st.button("🪄 生成草稿", type="primary"):
            st.session_state["_title_formula"] = title_formula
            st.session_state["_structure_logic"] = structure_logic
            try:
                from skills.copywriter import CopywriterSkill

                with st.spinner("✍️ Writer 正在生成草稿（参考：知识库+标题+分析文案+人设设定）…"):
                    cw = CopywriterSkill()
                    draft = cw.generate_copy(
                        title_formula=title_formula or "（参考分析结果自行拟定标题公式）",
                        structure_logic=structure_logic or "（参考分析结果自行组织结构）",
                        persona_name=persona,
                        additional_context=reference_content if reference_content else None,
                    )
                st.session_state["_draft"] = draft
                st.success("✅ 草稿生成完成！")
                
                # 如果输入了结构逻辑反馈，保存到记忆库
                if structure_logic.strip():
                    try:
                        # 获取最后生成的 work_id（如果有）
                        work_id = None
                        # 尝试从数据库获取最后一条生成的文案 ID
                        try:
                            with sqlite3.connect(db.db_path, check_same_thread=False) as conn:
                                cursor = conn.cursor()
                                cursor.execute("""
                                    SELECT id FROM generated_works 
                                    WHERE persona_name = ? 
                                    ORDER BY created_at DESC 
                                    LIMIT 1
                                """, (persona,))
                                row = cursor.fetchone()
                                if row:
                                    work_id = row[0]
                        except:
                            pass
                        
                        # 保存反馈到记忆库（rating=5 表示这是结构逻辑要求，不是负面反馈）
                        db.log_feedback(
                            work_id=work_id,
                            persona_name=persona,
                            rating=5,  # 5分表示这是结构要求，不是负面反馈
                            feedback_text=f"结构逻辑要求：{structure_logic.strip()}",
                        )
                        st.info("💾 结构逻辑反馈已保存到记忆库，将在后续生成时参考")
                    except Exception as e:
                        st.warning(f"⚠️ 保存反馈到记忆库失败：{e}（不影响草稿生成）")
            except Exception as e:
                st.error(f"生成草稿失败：{e}")

        with st.expander("🧪 离线测试（不调用模型）", expanded=False):
            if st.button("🧪 填入示例草稿"):
                st.session_state["_draft"] = (
                    "标题：别再瞎起标题了！这套公式让你点击率翻倍\n\n"
                    "开头：你是不是也写过“干货分享”，结果没人点？\n"
                    "痛点：标题不抓人 = 内容再好也白搭\n"
                    "方法：给你 3 套可复制标题公式 + 结构模板\n"
                    "结尾：评论区回“标题”我发你模板\n"
                )
                st.success("✅ 已写入草稿（_draft）")

        draft_text = (st.session_state.get("_draft") or "").strip()
        if draft_text:
            st.text_area("📌 草稿内容（只读预览）", value=draft_text, height=520, disabled=True)
        else:
            st.info("点击 **生成草稿** 后会在此显示")

    with col2:
        st.markdown("### ✅ 定稿（可编辑）")
        default_final = (st.session_state.get("final_copy") or "").strip() or (
            st.session_state.get("_draft") or ""
        )
        final = st.text_area("📝 在这里修改并定稿", value=default_final, height=720)

        save_col1, save_col2 = st.columns([1, 1])
        with save_col1:
            if st.button("💾 保存定稿", type="primary"):
                st.session_state["final_copy"] = final
                st.success("✅ 已保存到 final_copy。请切换到 **🎨 视觉（Visual）**")
        with save_col2:
            if st.button("🧹 清空定稿"):
                st.session_state["final_copy"] = ""
                st.info("已清空")


def _visual_plan_to_markdown(plans: List[Dict[str, Any]], final_copy: str) -> str:
    lines = [
        "# 视觉策划方案",
        "",
        f"**总页数**：{len(plans)}",
        "",
        "---",
        "",
        "## 定稿文案",
        "",
        "```",
        final_copy.strip(),
        "```",
        "",
        "---",
        "",
    ]
    for p in plans:
        idx = p.get("page_index")
        lines.extend(
            [
                f"## 第 {idx} 页",
                "",
                "### 🎨 画面描述（中文）",
                "",
                str(p.get("scene_description", "") or "").strip(),
                "",
                "### ✍️ 文字内容（原文）",
                "",
                str(p.get("text_content", "") or "").strip(),
                "",
                "### 🎯 排版建议",
                "",
                str(p.get("layout_suggestion", "") or "").strip(),
                "",
                "### 📝 英文绘画提示词",
                "",
                "```",
                str(p.get("image_prompt", "") or "").strip(),
                "```",
                "",
                "---",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def page_visual() -> None:
    st.subheader("🎨 视觉台（Visual）")

    final_copy = (st.session_state.get("final_copy") or "").strip()
    if not final_copy:
        st.warning("请先在 ✍️ 写作台完成定稿")
        return

    with st.expander("🧾 定稿文案（可折叠）", expanded=False):
        st.text(final_copy)

    if st.button("🎨 生成视觉方案（Illustrator）", type="primary"):
        try:
            from skills.illustrator import IllustratorSkill

            with st.spinner("🎨 正在生成视觉策划（可能需要 15-60 秒）…"):
                illustrator = IllustratorSkill()
                plans = illustrator.plan_visual_prompts(final_copy)

            st.session_state["visual_plan"] = plans
            st.success("✅ 视觉方案生成完成！")
        except Exception as e:
            st.error(f"生成失败：{e}")

    plans: List[Dict[str, Any]] = st.session_state.get("visual_plan", [])
    if not plans:
        st.info("点击 **生成视觉方案** 后会在此展示 tabs")
        with st.expander("🧪 离线测试（不调用模型）", expanded=False):
            if st.button("🧪 填入示例视觉方案（3 页）"):
                st.session_state["visual_plan"] = [
                    {
                        "page_index": 1,
                        "scene_description": "封面：大标题 + 3 个要点，温暖纸纹理，手绘便签风。",
                        "image_prompt": "Top-down view, Warm sand-colored dotted paper texture, Soft studio lighting, High resolution, Cozy sketchnote style, Marker pen outlines, Colored pencil shading, Slightly irregular lines, Dark green card with white logo, Black matte envelope, bright lighting, high key exposure",
                        "text_content": "别再瞎起标题了！这套公式让你点击率翻倍",
                        "layout_suggestion": "大标题居中，上方小贴纸点缀，右下角插画",
                    },
                    {
                        "page_index": 2,
                        "scene_description": "内页：痛点放大 + 对比示例，左文右图。",
                        "image_prompt": "Top-down view, Warm sand-colored dotted paper texture, Soft studio lighting, High resolution, Cozy sketchnote style, Marker pen outlines, Colored pencil shading, Slightly irregular lines, Dark green card with white logo, Black matte envelope",
                        "text_content": "标题不抓人 = 内容再好也白搭",
                        "layout_suggestion": "左侧文字块，右侧对比小卡片",
                    },
                    {
                        "page_index": 3,
                        "scene_description": "内页：公式清单 + 行动召唤，列表排版。",
                        "image_prompt": "Top-down view, Warm sand-colored dotted paper texture, Soft studio lighting, High resolution, Cozy sketchnote style, Marker pen outlines, Colored pencil shading, Slightly irregular lines, Dark green card with white logo, Black matte envelope",
                        "text_content": "评论区回“标题”我发你模板",
                        "layout_suggestion": "列表在上，CTA 在底部，用荧光笔强调",
                    },
                ]
                st.success("✅ 已写入 visual_plan，可直接查看 tabs")
        return

    tabs = st.tabs([f"📄 第 {p.get('page_index')} 页" for p in plans])
    for tab, p in zip(tabs, plans):
        with tab:
            st.markdown("### 🎨 画面描述")
            st.write(p.get("scene_description", ""))

            st.markdown("### 🎯 排版建议")
            st.write(p.get("layout_suggestion", ""))

            with st.expander("✍️ 本页文字内容（原文引用）", expanded=False):
                st.text(str(p.get("text_content", "") or ""))

            st.markdown("### 📝 Prompt（英文）")
            st.code(str(p.get("image_prompt", "") or ""), language="")

    md = _visual_plan_to_markdown(plans, final_copy)
    with st.expander("📄 Markdown 版本（复制/保存）", expanded=False):
        st.code(md, language="markdown")


def main() -> None:
    _render_header()
    _init_session_state()

    # 允许在 UI 内配置 Gemini Key（仅写入当前进程环境变量，不落盘）
    with st.sidebar.expander("🔐 Gemini API（预设默认）", expanded=False):
        # 加载用户设置（预设默认值）
        user_settings = _load_user_settings()
        
        # ✅ 优先从环境变量读取，其次从预设配置读取
        current_key = (
            st.session_state.get("_gemini_api_key", "") 
            or os.getenv("GEMINI_API_KEY", "").strip() 
            or user_settings.get("gemini_api_key", "").strip()
        )
        if current_key and "_gemini_api_key" not in st.session_state:
            st.session_state["_gemini_api_key"] = current_key
            os.environ["GEMINI_API_KEY"] = current_key
        
        if current_key:
            st.success(f"✅ 已自动加载预设默认值（长度：{len(current_key)} 字符）")
            st.caption("💡 已从预设配置自动加载，无需手动输入。如需更换，请在下方输入新 Key 并保存。")
        else:
            st.info("💡 提示：输入 API Key 后点击保存，下次启动会自动加载。")
            st.caption("用于 🧠/✍️/🎨 阶段调用 Gemini。")
        
        key_in = st.text_input(
            "GEMINI_API_KEY",
            value=current_key,
            type="password",
            help="输入后点击保存，下次启动会自动加载为预设默认值。",
        )
        if st.button("💾 保存为预设默认值", type="primary", key="save_gemini_api_key"):
            new_key = key_in.strip()
            st.session_state["_gemini_api_key"] = new_key
            if new_key:
                os.environ["GEMINI_API_KEY"] = new_key
                # 保存到配置文件
                user_settings["gemini_api_key"] = new_key
                _save_user_settings(user_settings)
                st.success("✅ 已保存为预设默认值，下次启动会自动加载！")
            else:
                # 清空时，也清空环境变量和配置文件
                if "GEMINI_API_KEY" in os.environ:
                    del os.environ["GEMINI_API_KEY"]
                user_settings["gemini_api_key"] = ""
                _save_user_settings(user_settings)
                st.warning("Key 已清空")

    page = st.sidebar.radio(
        "🧭 导航",
        options=["🛸 侦察 (Scout)", "🧠 分析 (Analyst)", "✍️ 写作 (Writer)", "🎨 视觉 (Visual)"],
        index=0,
    )

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 🧠 流水线状态")
    st.sidebar.caption(f"🛸 Scout 结果：{len(st.session_state.get('scout_results') or [])}")
    st.sidebar.caption(f"🧾 素材：{len(st.session_state.get('selected_materials') or [])}")
    st.sidebar.caption(f"📄 策略：{'✅' if (st.session_state.get('strategy_report') or '').strip() else '—'}")
    st.sidebar.caption(f"✍️ 定稿：{'✅' if (st.session_state.get('final_copy') or '').strip() else '—'}")
    st.sidebar.caption(f"🎨 视觉：{len(st.session_state.get('visual_plan') or [])}")

    if page.startswith("🛸"):
        page_scout()
    elif page.startswith("🧠"):
        page_analyst()
    elif page.startswith("✍️"):
        page_writer()
    else:
        page_visual()


if __name__ == "__main__":
    main()

