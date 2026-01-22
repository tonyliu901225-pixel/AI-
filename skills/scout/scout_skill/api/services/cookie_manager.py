# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Cookie management service for automatic cookie persistence

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from ..schemas import CookieInfo


class CookieManager:
    """Cookie management service"""

    def __init__(self):
        # Cookie storage directory
        self._cookie_dir = Path(__file__).parent.parent.parent / "cookies"
        self._cookie_dir.mkdir(exist_ok=True)

    def _get_cookie_file(self, platform: str) -> Path:
        """Get cookie file path for platform"""
        return self._cookie_dir / f"{platform}_cookies.json"

    def save_cookies(self, platform: str, cookies: str, expires_at: Optional[str] = None) -> bool:
        """
        Save cookies for a platform
        
        Args:
            platform: Platform name (xhs, dy, etc.)
            cookies: Cookie string
            expires_at: Optional expiration time
            
        Returns:
            True if saved successfully
        """
        try:
            cookie_info = CookieInfo(
                platform=platform,
                cookies=cookies,
                expires_at=expires_at,
                saved_at=datetime.now().isoformat(),
                is_valid=True
            )
            
            cookie_file = self._get_cookie_file(platform)
            with open(cookie_file, 'w', encoding='utf-8') as f:
                json.dump(cookie_info.model_dump(), f, ensure_ascii=False, indent=2)
            
            return True
        except Exception as e:
            print(f"[CookieManager] Error saving cookies: {e}")
            return False

    def load_cookies(self, platform: str) -> Optional[str]:
        """
        Load saved cookies for a platform
        
        Args:
            platform: Platform name
            
        Returns:
            Cookie string if found and valid, None otherwise
        """
        try:
            cookie_file = self._get_cookie_file(platform)
            if not cookie_file.exists():
                return None
            
            with open(cookie_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                cookie_info = CookieInfo(**data)
            
            # Check if cookies are still valid
            if not cookie_info.is_valid:
                return None
            
            # Check expiration if provided
            if cookie_info.expires_at:
                expires = datetime.fromisoformat(cookie_info.expires_at)
                if datetime.now() > expires:
                    return None
            
            return cookie_info.cookies
        except Exception as e:
            print(f"[CookieManager] Error loading cookies: {e}")
            return None

    def get_cookie_info(self, platform: str) -> Optional[CookieInfo]:
        """Get cookie information"""
        try:
            cookie_file = self._get_cookie_file(platform)
            if not cookie_file.exists():
                return None
            
            with open(cookie_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return CookieInfo(**data)
        except Exception as e:
            print(f"[CookieManager] Error getting cookie info: {e}")
            return None

    def delete_cookies(self, platform: str) -> bool:
        """Delete saved cookies for a platform"""
        try:
            cookie_file = self._get_cookie_file(platform)
            if cookie_file.exists():
                cookie_file.unlink()
            return True
        except Exception as e:
            print(f"[CookieManager] Error deleting cookies: {e}")
            return False

    def list_platforms(self) -> List[str]:
        """List all platforms with saved cookies"""
        platforms = []
        for file in self._cookie_dir.glob("*_cookies.json"):
            platform = file.stem.replace("_cookies", "")
            platforms.append(platform)
        return platforms

    def extract_cookies_from_browser(self, browser_data_dir: Path, platform: str) -> Optional[str]:
        """
        Extract cookies from browser data directory
        
        Args:
            browser_data_dir: Path to browser user data directory
            platform: Platform name
            
        Returns:
            Cookie string if extracted successfully
        """
        try:
            # This is a placeholder - actual implementation would need to:
            # 1. Read cookies from browser's cookie database
            # 2. Filter cookies relevant to the platform
            # 3. Format as cookie string
            
            # For now, we rely on the browser context cookies being saved
            # via SAVE_LOGIN_STATE mechanism
            return None
        except Exception as e:
            print(f"[CookieManager] Error extracting cookies: {e}")
            return None


# Global singleton
cookie_manager = CookieManager()
