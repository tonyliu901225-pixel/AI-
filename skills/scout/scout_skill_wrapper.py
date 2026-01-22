"""
ScoutSkill Wrapper - 提供同步接口给异步爬虫

目的：
- 对外只暴露一个简单的 run(keyword) 方法
- 内部处理 cwd / sys.path / asyncio 事件循环等细节
"""

import asyncio
import os
import sys
from typing import Optional


# MediaCrawler（原始爬虫工程）位于 skills/scout/scout_skill/ 下
_scout_skill_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "scout_skill"))

# 确保可以 import 到 scout_skill 里的模块（例如 config、media_platform.xhs 等）
if _scout_skill_dir not in sys.path:
    sys.path.insert(0, _scout_skill_dir)


_config = None
_XiaoHongShuCrawler = None
_utils = None


def _import_modules():
    """
    延迟导入：避免 import 时就依赖 cwd，且减少重复导入开销
    """
    global _config, _XiaoHongShuCrawler, _utils

    if _config is None or _XiaoHongShuCrawler is None or _utils is None:
        original_cwd = os.getcwd()
        try:
            # 许多相对路径（如 libs/stealth.min.js、data/xxx）依赖在 scout_skill 目录下运行
            os.chdir(_scout_skill_dir)

            import config as _config_module
            from media_platform.xhs import XiaoHongShuCrawler as _XiaoHongShuCrawler_class
            from tools import utils as _utils_module

            _config = _config_module
            _XiaoHongShuCrawler = _XiaoHongShuCrawler_class
            _utils = _utils_module
        finally:
            os.chdir(original_cwd)

    return _config, _XiaoHongShuCrawler, _utils


class ScoutSkill:
    """
    对外同步接口：ScoutSkill().run(keyword)
    """

    def __init__(self):
        _import_modules()
        self.crawler: Optional[object] = None
        _utils.logger.info("[ScoutSkill] 初始化完成")

    def run(self, keyword: str) -> None:
        config, _, utils = _import_modules()

        original_cwd = os.getcwd()
        try:
            os.chdir(_scout_skill_dir)

            # 注入关键词与爬虫模式（搜索）
            config.KEYWORDS = keyword
            config.CRAWLER_TYPE = "search"
            config.PLATFORM = "xhs"

            utils.logger.info(f"[ScoutSkill] 开始抓取关键词: {keyword}")
        finally:
            os.chdir(original_cwd)

        try:
            asyncio.run(self._run_async())
        except KeyboardInterrupt:
            _utils.logger.info("[ScoutSkill] 用户中断了爬虫运行")
        except Exception as e:
            _utils.logger.error(f"[ScoutSkill] 爬虫运行出错: {e}")
            raise

    async def _run_async(self) -> None:
        _, XiaoHongShuCrawler, utils = _import_modules()

        original_cwd = os.getcwd()
        try:
            os.chdir(_scout_skill_dir)

            self.crawler = XiaoHongShuCrawler()
            await self.crawler.start()
            await self.crawler.close()
        finally:
            os.chdir(original_cwd)

        utils.logger.info("[ScoutSkill] 爬虫运行完成")

