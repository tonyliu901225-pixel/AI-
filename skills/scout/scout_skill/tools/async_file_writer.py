# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tools/async_file_writer.py
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
import csv
import json
import os
import pathlib
from typing import Dict, List
import aiofiles
import config
from tools.utils import utils
from tools.words import AsyncWordCloudGenerator

class AsyncFileWriter:
    def __init__(self, platform: str, crawler_type: str):
        self.lock = asyncio.Lock()
        self.platform = platform
        self.crawler_type = crawler_type
        self.wordcloud_generator = AsyncWordCloudGenerator() if config.ENABLE_GET_WORDCLOUD else None

    def _get_file_path(self, file_type: str, item_type: str) -> str:
        base_path = f"data/{self.platform}/{file_type}"
        pathlib.Path(base_path).mkdir(parents=True, exist_ok=True)
        file_name = f"{self.crawler_type}_{item_type}_{utils.get_current_date()}.{file_type}"
        return f"{base_path}/{file_name}"

    async def write_to_csv(self, item: Dict, item_type: str):
        file_path = self._get_file_path('csv', item_type)
        async with self.lock:
            # 自动去重：先读取现有数据，检查是否存在重复项
            existing_items = []
            file_exists = os.path.exists(file_path)
            
            # 确定唯一标识字段
            unique_key_field = None
            if item_type == "contents":
                unique_key_field = "note_id" if "note_id" in item else ("aweme_id" if "aweme_id" in item else "video_id")
            elif item_type == "comments":
                unique_key_field = "comment_id"
            elif item_type == "creators":
                unique_key_field = "user_id" if "user_id" in item else "creator_id"
            
            # 如果文件存在且有唯一标识字段，读取现有数据检查重复
            if file_exists and unique_key_field and unique_key_field in item:
                unique_key_value = item.get(unique_key_field)
                try:
                    async with aiofiles.open(file_path, 'r', encoding='utf-8-sig') as f:
                        content = await f.read()
                        if content:
                            lines = content.strip().splitlines()
                            if lines:
                                reader = csv.DictReader(lines)
                                existing_items = list(reader)
                                
                                # 检查是否已存在相同的记录
                                for existing_item in existing_items:
                                    if existing_item.get(unique_key_field) == unique_key_value:
                                        utils.logger.debug(f"[AsyncFileWriter.write_to_csv] Skipped duplicate {item_type} with {unique_key_field}: {unique_key_value}")
                                        return  # 跳过重复项
                except Exception as e:
                    utils.logger.warning(f"[AsyncFileWriter.write_to_csv] Error reading existing CSV for deduplication: {e}")
                    existing_items = []
            
            # 写入新数据
            async with aiofiles.open(file_path, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=item.keys())
                if not file_exists or await f.tell() == 0:
                    await writer.writeheader()
                await writer.writerow(item)
                utils.logger.debug(f"[AsyncFileWriter.write_to_csv] Added new {item_type} with {unique_key_field}: {item.get(unique_key_field) if unique_key_field else 'N/A'}")

    async def write_single_item_to_json(self, item: Dict, item_type: str):
        file_path = self._get_file_path('json', item_type)
        async with self.lock:
            existing_data = []
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    try:
                        content = await f.read()
                        if content:
                            existing_data = json.loads(content)
                        if not isinstance(existing_data, list):
                            existing_data = [existing_data]
                    except json.JSONDecodeError:
                        existing_data = []

            # 自动去重：基于note_id/content_id/comment_id等唯一标识
            # 对于不同item_type使用不同的唯一标识字段
            unique_key = None
            if item_type == "contents":
                unique_key = item.get("note_id") or item.get("aweme_id") or item.get("video_id")
            elif item_type == "comments":
                unique_key = item.get("comment_id")
            elif item_type == "creators":
                unique_key = item.get("user_id") or item.get("creator_id")
            
            # 检查是否已存在
            is_duplicate = False
            if unique_key and existing_data:
                for existing_item in existing_data:
                    existing_unique_key = None
                    if item_type == "contents":
                        existing_unique_key = existing_item.get("note_id") or existing_item.get("aweme_id") or existing_item.get("video_id")
                    elif item_type == "comments":
                        existing_unique_key = existing_item.get("comment_id")
                    elif item_type == "creators":
                        existing_unique_key = existing_item.get("user_id") or existing_item.get("creator_id")
                    
                    if existing_unique_key == unique_key:
                        is_duplicate = True
                        # 更新已存在的数据（保留旧数据，更新字段）
                        existing_item.update(item)
                        utils.logger.debug(f"[AsyncFileWriter.write_single_item_to_json] Updated existing {item_type} with unique_key: {unique_key}")
                        break
            
            if not is_duplicate:
                existing_data.append(item)
                utils.logger.debug(f"[AsyncFileWriter.write_single_item_to_json] Added new {item_type} with unique_key: {unique_key}")
            else:
                utils.logger.debug(f"[AsyncFileWriter.write_single_item_to_json] Skipped duplicate {item_type} with unique_key: {unique_key}")

            async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(existing_data, ensure_ascii=False, indent=4))

    async def generate_wordcloud_from_comments(self):
        """
        Generate wordcloud from comments data
        Only works when ENABLE_GET_WORDCLOUD and ENABLE_GET_COMMENTS are True
        """
        if not config.ENABLE_GET_WORDCLOUD or not config.ENABLE_GET_COMMENTS:
            return

        if not self.wordcloud_generator:
            return

        try:
            # Read comments from JSON file
            comments_file_path = self._get_file_path('json', 'comments')
            if not os.path.exists(comments_file_path) or os.path.getsize(comments_file_path) == 0:
                utils.logger.info(f"[AsyncFileWriter.generate_wordcloud_from_comments] No comments file found at {comments_file_path}")
                return

            async with aiofiles.open(comments_file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                if not content:
                    utils.logger.info(f"[AsyncFileWriter.generate_wordcloud_from_comments] Comments file is empty")
                    return

                comments_data = json.loads(content)
                if not isinstance(comments_data, list):
                    comments_data = [comments_data]

            # Filter comments data to only include 'content' field
            # Handle different comment data structures across platforms
            filtered_data = []
            for comment in comments_data:
                if isinstance(comment, dict):
                    # Try different possible content field names
                    content_text = comment.get('content') or comment.get('comment_text') or comment.get('text') or ''
                    if content_text:
                        filtered_data.append({'content': content_text})

            if not filtered_data:
                utils.logger.info(f"[AsyncFileWriter.generate_wordcloud_from_comments] No valid comment content found")
                return

            # Generate wordcloud
            words_base_path = f"data/{self.platform}/words"
            pathlib.Path(words_base_path).mkdir(parents=True, exist_ok=True)
            words_file_prefix = f"{words_base_path}/{self.crawler_type}_comments_{utils.get_current_date()}"

            utils.logger.info(f"[AsyncFileWriter.generate_wordcloud_from_comments] Generating wordcloud from {len(filtered_data)} comments")
            await self.wordcloud_generator.generate_word_frequency_and_cloud(filtered_data, words_file_prefix)
            utils.logger.info(f"[AsyncFileWriter.generate_wordcloud_from_comments] Wordcloud generated successfully at {words_file_prefix}")

        except Exception as e:
            utils.logger.error(f"[AsyncFileWriter.generate_wordcloud_from_comments] Error generating wordcloud: {e}")
