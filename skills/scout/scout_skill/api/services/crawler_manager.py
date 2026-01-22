# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/services/crawler_manager.py
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
import subprocess
import signal
import os
import logging
from typing import Optional, List
from datetime import datetime
from pathlib import Path

from ..schemas import CrawlerStartRequest, LogEntry

logger = logging.getLogger(__name__)


class CrawlerManager:
    """Crawler process manager"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self.process: Optional[subprocess.Popen] = None
        self.status = "idle"
        self.started_at: Optional[datetime] = None
        self.current_config: Optional[CrawlerStartRequest] = None
        self._log_id = 0
        self._logs: List[LogEntry] = []
        self._read_task: Optional[asyncio.Task] = None
        # Project root directory
        self._project_root = Path(__file__).parent.parent.parent
        # Log queue - for pushing to WebSocket
        self._log_queue: Optional[asyncio.Queue] = None
        # Progress tracking
        self._progress = 0.0
        self._current_count = 0
        self._total_count = 0
        self._current_page = 1
        self._current_message = ""

    @property
    def logs(self) -> List[LogEntry]:
        return self._logs

    def get_log_queue(self) -> asyncio.Queue:
        """Get or create log queue"""
        if self._log_queue is None:
            self._log_queue = asyncio.Queue()
        return self._log_queue

    def _create_log_entry(self, message: str, level: str = "info", category: Optional[str] = None) -> LogEntry:
        """Create log entry"""
        self._log_id += 1
        entry = LogEntry(
            id=self._log_id,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            level=level,
            message=message,
            category=category
        )
        self._logs.append(entry)
        # Keep last 500 logs
        if len(self._logs) > 500:
            self._logs = self._logs[-500:]
        return entry

    async def _push_log(self, entry: LogEntry):
        """Push log to queue"""
        if self._log_queue is not None:
            try:
                self._log_queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass

    def _parse_log_level(self, line: str) -> tuple[str, Optional[str]]:
        """Parse log level and category"""
        line_upper = line.upper()
        category = None
        
        # Determine category
        if any(keyword in line_upper for keyword in ["LOGIN", "登录", "QRCODE", "COOKIE"]):
            category = "login"
        elif any(keyword in line_upper for keyword in ["CRAWL", "爬取", "SEARCH", "搜索"]):
            category = "crawl"
        elif any(keyword in line_upper for keyword in ["SAVE", "保存", "STORE", "存储"]):
            category = "save"
        elif any(keyword in line_upper for keyword in ["NETWORK", "网络", "REQUEST", "HTTP"]):
            category = "network"
        elif "ERROR" in line_upper or "FAILED" in line_upper:
            category = "error"
        
        # Determine level
        if "ERROR" in line_upper or "FAILED" in line_upper or "异常" in line:
            level = "error"
        elif "WARNING" in line_upper or "WARN" in line_upper or "警告" in line:
            level = "warning"
        elif "SUCCESS" in line_upper or "完成" in line or "成功" in line:
            level = "success"
        elif "DEBUG" in line_upper:
            level = "debug"
        else:
            level = "info"
            
        return level, category

    async def start(self, config: CrawlerStartRequest) -> bool:
        """Start crawler process"""
        async with self._lock:
            if self.process and self.process.poll() is None:
                return False

            # Clear old logs
            self._logs = []
            self._log_id = 0
            # Reset progress
            self._progress = 0.0
            self._current_count = 0
            self._total_count = config.max_count if config.max_count > 0 else 150
            self._current_page = config.start_page
            self._current_message = "准备启动..."

            # Clear pending queue (don't replace object to avoid WebSocket broadcast coroutine holding old queue reference)
            if self._log_queue is None:
                self._log_queue = asyncio.Queue()
            else:
                try:
                    while True:
                        self._log_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            # Build command line arguments
            cmd = self._build_command(config)
            
            # Log start information
            entry = self._create_log_entry(f"启动爬虫: {' '.join(cmd[:5])}...", "info", "crawl")
            await self._push_log(entry)

            # Auto-load cookies if enabled and cookies not provided
            try:
                from .cookie_manager import cookie_manager
                if config.auto_save_cookie and not config.cookies:
                    saved_cookies = cookie_manager.load_cookies(config.platform.value)
                    if saved_cookies:
                        config.cookies = saved_cookies
                        # Rebuild command with loaded cookies
                        cmd = self._build_command(config)
                        entry = self._create_log_entry(
                            f"Loaded saved cookies for {config.platform.value}",
                            "info",
                            "login"
                        )
                        await self._push_log(entry)
            except ImportError:
                # Cookie manager not available, skip auto-load
                pass

            try:
                # CRITICAL: Ensure UTF-8 encoding for subprocess
                # Set environment variables to ensure proper encoding
                env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "LC_ALL": "en_US.UTF-8"}
                
                # Log the command being executed (first few args for debugging)
                logger.info(f"[CrawlerManager] 🚀 Starting subprocess with command: {' '.join(cmd[:8])} ...")
                if '--keywords' in cmd:
                    keywords_idx = cmd.index('--keywords')
                    if keywords_idx >= 0 and keywords_idx + 1 < len(cmd):
                        logger.info(f"[CrawlerManager] 🔍 Keywords in command array: {repr(cmd[keywords_idx+1])}")
                
                # Start subprocess
                # 使用text=False手动解码，以便处理编码错误
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=False,  # 不使用text模式，手动解码
                    bufsize=1,
                    cwd=str(self._project_root),
                    env=env
                )

                self.status = "running"
                self.started_at = datetime.now()
                self.current_config = config

                entry = self._create_log_entry(
                    f"Crawler started on platform: {config.platform.value}, type: {config.crawler_type.value}",
                    "success"
                )
                await self._push_log(entry)

                # Start log reading task
                self._read_task = asyncio.create_task(self._read_output())

                return True
            except Exception as e:
                self.status = "error"
                entry = self._create_log_entry(f"Failed to start crawler: {str(e)}", "error")
                await self._push_log(entry)
                return False

    async def stop(self) -> bool:
        """Stop crawler process"""
        async with self._lock:
            if not self.process or self.process.poll() is not None:
                return False

            self.status = "stopping"
            entry = self._create_log_entry("Sending SIGTERM to crawler process...", "warning")
            await self._push_log(entry)

            try:
                self.process.send_signal(signal.SIGTERM)

                # Wait for graceful exit (up to 15 seconds)
                for _ in range(30):
                    if self.process.poll() is not None:
                        break
                    await asyncio.sleep(0.5)

                # If still not exited, force kill
                if self.process.poll() is None:
                    entry = self._create_log_entry("Process not responding, sending SIGKILL...", "warning")
                    await self._push_log(entry)
                    self.process.kill()

                entry = self._create_log_entry("Crawler process terminated", "info")
                await self._push_log(entry)

            except Exception as e:
                entry = self._create_log_entry(f"Error stopping crawler: {str(e)}", "error")
                await self._push_log(entry)

            self.status = "idle"
            self.current_config = None

            # Cancel log reading task
            if self._read_task:
                self._read_task.cancel()
                self._read_task = None

            return True

    def _extract_progress_from_log(self, line: str):
        """Extract progress information from log line"""
        import re
        from pathlib import Path
        
        # Try to extract count information (e.g., "已爬取 10/100", "爬取第 5 页", "10/150")
        count_match = re.search(r'已?爬取.*?(\d+)\s*[/:]\s*(\d+)', line)
        if count_match:
            self._current_count = int(count_match.group(1))
            self._total_count = int(count_match.group(2))
            if self._total_count > 0:
                self._progress = (self._current_count / self._total_count) * 100.0
                self._current_message = f"已爬取 {self._current_count}/{self._total_count} 条"
        
        # Try to extract page information - 多种格式
        page_match = re.search(r'(?:page|页)[:：]?\s*(\d+)|第\s*(\d+)\s*页', line, re.IGNORECASE)
        if page_match:
            page_num = int(page_match.group(1) or page_match.group(2))
            self._current_page = page_num
            self._current_message = f"正在爬取第 {page_num} 页"
        
        # 从日志中提取页数（例如 "search Xiaohongshu keyword: ..., page: 2"）
        page_keyword_match = re.search(r'search.*?page[:\s]+(\d+)', line, re.IGNORECASE)
        if page_keyword_match:
            page_num = int(page_keyword_match.group(1))
            self._current_page = page_num
            self._current_message = f"正在爬取第 {page_num} 页"
        
        # 从日志中提取页数（例如 "search Xiaohongshu keyword: ..., page: 2"）
        page_keyword_match = re.search(r'search.*?page[:\s]+(\d+)', line, re.IGNORECASE)
        if page_keyword_match:
            page_num = int(page_keyword_match.group(1))
            self._current_page = page_num
            self._current_message = f"正在爬取第 {page_num} 页"
        
        # 检测到保存数据时，尝试从数据文件计算实际进度
        if "store_content" in line.lower() or "update_xhs_note" in line.lower() or "saved note" in line.lower():
            try:
                # 统计实际保存的数据数量
                data_dir = Path(__file__).parent.parent.parent / "data"
                if data_dir.exists() and self.current_config:
                    platform = self.current_config.platform.value
                    from datetime import datetime
                    today = datetime.now().strftime("%Y-%m-%d")
                    contents_file = data_dir / platform / "json" / f"search_contents_{today}.json"
                    
                    if contents_file.exists():
                        import json
                        with open(contents_file, 'r', encoding='utf-8') as f:
                            try:
                                contents_data = json.load(f)
                                if isinstance(contents_data, list):
                                    actual_count = len(contents_data)
                                    if actual_count > self._current_count:
                                        self._current_count = actual_count
                                        if self._total_count > 0:
                                            self._progress = min((self._current_count / self._total_count) * 100.0, 100.0)
                                            self._current_message = f"已爬取 {self._current_count}/{self._total_count} 条"
                            except (json.JSONDecodeError, Exception):
                                pass  # 忽略文件读取错误
            except Exception:
                pass  # 忽略进度统计错误
        
        # Update message from log content
        if "登录" in line or "login" in line.lower():
            self._current_message = "正在登录..."
        elif "搜索" in line or "search" in line.lower():
            if not self._current_message or "爬取" not in self._current_message:
                self._current_message = "正在搜索..."
        elif "保存" in line or "save" in line.lower() or "store" in line.lower():
            if not self._current_message or "保存" not in self._current_message:
                self._current_message = "正在保存数据..."

    def get_status(self) -> dict:
        """Get current status with progress information"""
        from pathlib import Path
        from datetime import datetime
        
        # 如果正在运行，尝试从实际数据文件更新进度
        if self.status == "running" and self.current_config:
            try:
                data_dir = Path(__file__).parent.parent.parent / "data"
                if data_dir.exists():
                    platform = self.current_config.platform.value
                    today = datetime.now().strftime("%Y-%m-%d")
                    contents_file = data_dir / platform / "json" / f"search_contents_{today}.json"
                    
                    if contents_file.exists():
                        import json
                        try:
                            with open(contents_file, 'r', encoding='utf-8') as f:
                                contents_data = json.load(f)
                                if isinstance(contents_data, list):
                                    actual_count = len(contents_data)
                                    # 更新进度（允许等于或大于，因为可能重新读取或新增数据）
                                    if actual_count >= self._current_count:
                                        self._current_count = actual_count
                                        if self._total_count > 0:
                                            self._progress = min((self._current_count / self._total_count) * 100.0, 100.0)
                                            self._current_message = f"已爬取 {self._current_count}/{self._total_count} 条"
                                            logger.info(f"[CrawlerManager.get_status] ✅ 从数据文件更新进度: {self._current_count}/{self._total_count} ({self._progress:.1f}%)")
                        except (json.JSONDecodeError, Exception) as e:
                            # 文件可能正在写入，忽略错误
                            pass
            except Exception as e:
                logger.debug(f"[CrawlerManager.get_status] 更新进度失败: {e}")
        
        status_dict = {
            "status": self.status,
            "platform": self.current_config.platform.value if self.current_config else None,
            "crawler_type": self.current_config.crawler_type.value if self.current_config else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "error_message": None,
            "progress": round(self._progress, 2) if self._progress > 0 or (self._current_count > 0) else None,
            "current_count": self._current_count if self._current_count > 0 else None,
            "total_count": self._total_count if self._total_count > 0 else None,
            "current_page": self._current_page if self._current_page and self._current_page > 0 else None,
            "message": self._current_message if self._current_message else None
        }
        return status_dict

    def _build_command(self, config: CrawlerStartRequest) -> list:
        """Build main.py command line arguments"""
        cmd = ["uv", "run", "python", "main.py"]

        cmd.extend(["--platform", config.platform.value])
        cmd.extend(["--lt", config.login_type.value])
        cmd.extend(["--type", config.crawler_type.value])
        cmd.extend(["--save_data_option", config.save_option.value])

        # Pass different arguments based on crawler type
        # CRITICAL FIX: Always pass --keywords for search type, even if empty
        # This ensures cmd_arg can parse and update config.KEYWORDS correctly
        if config.crawler_type.value == "search":
            # Process keywords: strip whitespace
            keywords_raw = config.keywords if config.keywords is not None else ""
            keywords = keywords_raw.strip() if isinstance(keywords_raw, str) else ""
            
            # Debug logging
            logger.info(f"[CrawlerManager] 🔍 Keywords processing - raw: {repr(keywords_raw)}, stripped: {repr(keywords)}")
            
            # CRITICAL: Always pass --keywords parameter for search type
            # Even if empty, we pass it to ensure cmd_arg can process it
            # Use shlex.quote to properly handle special characters and encoding
            import shlex
            if keywords:
                # Properly quote keywords to handle spaces and special characters
                cmd.extend(["--keywords", keywords])
                logger.info(f"[CrawlerManager] ✅ Keywords added to command: {repr(keywords)} (length: {len(keywords)})")
                # Log the actual command that will be executed (first 500 chars)
                cmd_str = ' '.join(cmd[:10] + ['...'] if len(cmd) > 10 else cmd)
                logger.debug(f"[CrawlerManager] Command preview: {cmd_str[:200]}")
            else:
                # Don't pass --keywords parameter if empty
                # This way cmd_arg will use config.KEYWORDS default value
                logger.error(f"[CrawlerManager] ❌ Search type but keywords is EMPTY! Raw: {repr(keywords_raw)}")
                logger.error(f"[CrawlerManager] ❌ NOT passing --keywords parameter. Will use default from config: '送客户礼品推荐'")
                logger.error(f"[CrawlerManager] ❌ THIS IS A FRONTEND BUG - keywords not being sent correctly!")
        elif config.crawler_type.value == "detail" and config.specified_ids:
            cmd.extend(["--specified_id", config.specified_ids])
        elif config.crawler_type.value == "creator" and config.creator_ids:
            cmd.extend(["--creator_id", config.creator_ids])

            if config.start_page != 1:
                cmd.extend(["--start", str(config.start_page)])

        # Add max count parameter
        if config.max_count and config.max_count > 0:
            cmd.extend(["--max_count", str(config.max_count)])

        # Add sort type parameter (platform specific, e.g., xhs supports sort_type)
        if config.sort_type and config.sort_type.value:
            cmd.extend(["--sort_type", config.sort_type.value])

        cmd.extend(["--get_comment", "true" if config.enable_comments else "false"])
        cmd.extend(["--get_sub_comment", "true" if config.enable_sub_comments else "false"])

        if config.cookies:
            cmd.extend(["--cookies", config.cookies])

        cmd.extend(["--headless", "true" if config.headless else "false"])

        return cmd

    async def _read_output(self):
        """Asynchronously read process output"""
        loop = asyncio.get_event_loop()

        try:
            while self.process and self.process.poll() is None:
                # Read a line in thread pool
                line_bytes = await loop.run_in_executor(
                    None, self.process.stdout.readline
                )
                if line_bytes:
                    try:
                        # 尝试解码为UTF-8，使用errors='replace'处理无效字节
                        line = line_bytes.decode('utf-8', errors='replace').strip()
                    except Exception as e:
                        # 如果解码失败，记录错误并跳过这一行
                        logger.warning(f"[CrawlerManager._read_output] 解码输出失败: {str(e)[:100]}")
                        continue
                    
                    if line:
                        level, category = self._parse_log_level(line)
                        # Try to extract progress information from log
                        self._extract_progress_from_log(line)
                        # 定期从数据文件更新进度（每3条日志检查一次）
                        if len(self.logs) % 3 == 0:
                            try:
                                from pathlib import Path
                                from datetime import datetime
                                if self.current_config and self.status == "running":
                                    data_dir = Path(__file__).parent.parent.parent / "data"
                                    if data_dir.exists():
                                        platform = self.current_config.platform.value
                                        today = datetime.now().strftime("%Y-%m-%d")
                                        contents_file = data_dir / platform / "json" / f"search_contents_{today}.json"
                                        if contents_file.exists():
                                            import json
                                            try:
                                                with open(contents_file, 'r', encoding='utf-8') as f:
                                                    contents_data = json.load(f)
                                                    if isinstance(contents_data, list):
                                                        actual_count = len(contents_data)
                                                        if actual_count >= self._current_count:
                                                            self._current_count = actual_count
                                                            if self._total_count > 0:
                                                                self._progress = min((self._current_count / self._total_count) * 100.0, 100.0)
                                                                self._current_message = f"已爬取 {self._current_count}/{self._total_count} 条"
                                            except (json.JSONDecodeError, Exception):
                                                pass
                            except Exception:
                                pass
                        entry = self._create_log_entry(line, level, category)
                        await self._push_log(entry)

            # Read remaining output
            if self.process and self.process.stdout:
                try:
                    remaining = await loop.run_in_executor(
                        None, self.process.stdout.read
                    )
                    if remaining:
                        try:
                            # 尝试解码为UTF-8，使用errors='replace'处理无效字节
                            remaining_text = remaining.decode('utf-8', errors='replace')
                            for line in remaining_text.strip().split('\n'):
                                if line.strip():
                                    level, category = self._parse_log_level(line.strip())
                                    self._extract_progress_from_log(line.strip())
                                    entry = self._create_log_entry(line.strip(), level, category)
                                    await self._push_log(entry)
                        except Exception as e:
                            logger.warning(f"[CrawlerManager._read_output] 解码剩余输出失败: {str(e)[:100]}")
                except Exception as e:
                    logger.debug(f"[CrawlerManager._read_output] 读取剩余输出失败: {str(e)}")

            # Process ended
            if self.status == "running":
                exit_code = self.process.returncode if self.process else -1
                if exit_code == 0:
                    self._progress = 100.0
                    self._current_message = "爬取完成"
                    entry = self._create_log_entry("Crawler completed successfully", "success", "crawl")
                else:
                    self._current_message = f"爬取异常退出，代码: {exit_code}"
                    entry = self._create_log_entry(f"Crawler exited with code: {exit_code}", "warning", "error")
                await self._push_log(entry)
                self.status = "completed" if exit_code == 0 else "error"

        except asyncio.CancelledError:
            pass
        except Exception as e:
            entry = self._create_log_entry(f"Error reading output: {str(e)}", "error")
            await self._push_log(entry)


# Global singleton
crawler_manager = CrawlerManager()
