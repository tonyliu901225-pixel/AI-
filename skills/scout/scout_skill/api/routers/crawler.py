# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/routers/crawler.py
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

from fastapi import APIRouter, HTTPException
from typing import List

from ..schemas import CrawlerStartRequest, CrawlerStatusResponse, CookieInfo
from ..services import crawler_manager, cookie_manager

router = APIRouter(prefix="/crawler", tags=["crawler"])


@router.post("/start")
async def start_crawler(request: CrawlerStartRequest):
    """Start crawler task"""
    import logging
    logger = logging.getLogger(__name__)
    
    # CRITICAL FIX: Check if keywords is empty for search type
    # This handles the case where frontend doesn't properly send keywords
    if request.crawler_type.value == "search":
        keywords_raw = request.keywords if request.keywords is not None else ""
        keywords_cleaned = keywords_raw.strip() if isinstance(keywords_raw, str) else ""
        
        if not keywords_cleaned:
            logger.error(f"[API] ❌ CRITICAL: Search type but keywords is EMPTY! Received: {repr(request.keywords)}")
            logger.error(f"[API] ❌ Frontend did not send keywords correctly. Will use default from config: '送客户礼品推荐'")
            logger.error(f"[API] ❌ This is a FRONTEND bug. User entered keyword in UI but it's not being sent to API.")
            # Note: We still proceed, but crawler will use default keywords from config
            # This is expected behavior - empty keywords means use config default
        else:
            logger.info(f"[API] ✅ Keywords received successfully: {repr(keywords_cleaned)}")
    
    logger.info(f"[API] Received start request - keywords: {repr(request.keywords)}, crawler_type: {request.crawler_type.value}")
    
    success = await crawler_manager.start(request)
    if not success:
        # Handle concurrent/duplicate requests: if process is already running, return 400 instead of 500
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="Failed to start crawler")

    return {"status": "ok", "message": "Crawler started successfully"}


@router.post("/stop")
async def stop_crawler():
    """Stop crawler task"""
    success = await crawler_manager.stop()
    if not success:
        # Handle concurrent/duplicate requests: if process already exited/doesn't exist, return 400 instead of 500
        if not crawler_manager.process or crawler_manager.process.poll() is not None:
            raise HTTPException(status_code=400, detail="No crawler is running")
        raise HTTPException(status_code=500, detail="Failed to stop crawler")

    return {"status": "ok", "message": "Crawler stopped successfully"}


@router.get("/status", response_model=CrawlerStatusResponse)
async def get_crawler_status():
    """Get crawler status"""
    return crawler_manager.get_status()


@router.get("/logs")
async def get_logs(limit: int = 100):
    """Get recent logs"""
    logs = crawler_manager.logs[-limit:] if limit > 0 else crawler_manager.logs
    return {"logs": [log.model_dump() for log in logs]}


@router.get("/cookies/{platform}")
async def get_cookies(platform: str):
    """Get saved cookies for a platform"""
    cookie_info = cookie_manager.get_cookie_info(platform)
    if not cookie_info:
        raise HTTPException(status_code=404, detail=f"No saved cookies found for platform: {platform}")
    return cookie_info.model_dump()


@router.post("/cookies/{platform}")
async def save_cookies(platform: str, cookies: str, expires_at: str = None):
    """Save cookies for a platform"""
    success = cookie_manager.save_cookies(platform, cookies, expires_at)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save cookies")
    return {"status": "ok", "message": "Cookies saved successfully"}


@router.delete("/cookies/{platform}")
async def delete_cookies(platform: str):
    """Delete saved cookies for a platform"""
    success = cookie_manager.delete_cookies(platform)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete cookies")
    return {"status": "ok", "message": "Cookies deleted successfully"}


@router.get("/cookies")
async def list_cookies():
    """List all platforms with saved cookies"""
    platforms = cookie_manager.list_platforms()
    cookies_info = []
    for platform in platforms:
        info = cookie_manager.get_cookie_info(platform)
        if info:
            cookies_info.append(info.model_dump())
    return {"cookies": cookies_info}
