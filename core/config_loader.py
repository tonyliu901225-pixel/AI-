"""
配置加载器模块
负责加载和管理项目配置文件
"""

import yaml
import os
from pathlib import Path
from typing import Dict, Any


class ConfigManager:
    """配置管理器 - 负责加载和管理所有配置文件"""
    
    def __init__(self, config_dir: str = "config"):
        """
        初始化配置管理器
        
        Args:
            config_dir: 配置文件目录，默认为 config
        """
        # 如果传入的是相对路径，则转换为基于项目根目录的绝对路径
        # 说明：爬虫运行时可能会切换 cwd（例如进入 skills/scout/scout_skill），
        # 如果这里仍使用相对路径，会导致读取到错误位置的配置或回退到默认配置。
        if not os.path.isabs(config_dir):
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            config_dir = os.path.join(project_root, config_dir)

        self.config_dir = config_dir
        self._scout_rules = None
        self._analyst_rules = None
        self._copywriter_rules = None
        self._illustrator_rules = None
    
    def load_scout_rules(self) -> Dict[str, Any]:
        """
        加载情报 Agent 的规则配置
        
        Returns:
            Dict: 包含抓取行为、数据过滤和黑名单的配置字典
        """
        if self._scout_rules is None:
            # ✅ 支持通过环境变量指定测试规则文件（方便跑可复现的测试，不污染正式配置）
            # 用法示例：
            #   SCOUT_RULES_PATH=/abs/path/to/scout_rules_test.yaml python3 test_scout.py "宠物医生"
            env_rules_path = os.getenv("SCOUT_RULES_PATH", "").strip()
            if env_rules_path:
                config_path = env_rules_path
            else:
                config_path = os.path.join(self.config_dir, "scout_rules.yaml")
            
            # 如果配置文件不存在，使用默认配置
            if not os.path.exists(config_path):
                return self._get_default_scout_rules()
            
            with open(config_path, 'r', encoding='utf-8') as f:
                self._scout_rules = yaml.safe_load(f) or {}
        
        return self._scout_rules

    def load_analyst_rules(self) -> Dict[str, Any]:
        """
        加载智库 Analyst Agent 的规则配置（config/analyst_rules.yaml）

        Returns:
            Dict: 包含模型配置与 system_prompt 等的配置字典
        """
        if self._analyst_rules is None:
            # ✅ 支持通过环境变量指定规则文件（方便测试/多环境切换）
            # 用法示例：
            #   ANALYST_RULES_PATH=/abs/path/to/analyst_rules.yaml python3 your_script.py
            env_rules_path = os.getenv("ANALYST_RULES_PATH", "").strip()
            if env_rules_path:
                config_path = env_rules_path
            else:
                config_path = os.path.join(self.config_dir, "analyst_rules.yaml")

            if not os.path.exists(config_path):
                return self._get_default_analyst_rules()

            with open(config_path, "r", encoding="utf-8") as f:
                self._analyst_rules = yaml.safe_load(f) or {}

        return self._analyst_rules

    def load_copywriter_rules(self) -> Dict[str, Any]:
        """
        加载文案 Agent (Copywriter) 的规则配置（config/copywriter_rules.yaml）

        Returns:
            Dict: 包含模型配置与 RAG 配置等的配置字典
        """
        if self._copywriter_rules is None:
            # ✅ 支持通过环境变量指定规则文件（方便测试/多环境切换）
            # 用法示例：
            #   COPYWRITER_RULES_PATH=/abs/path/to/copywriter_rules.yaml python3 your_script.py
            env_rules_path = os.getenv("COPYWRITER_RULES_PATH", "").strip()
            if env_rules_path:
                config_path = env_rules_path
            else:
                config_path = os.path.join(self.config_dir, "copywriter_rules.yaml")

            if not os.path.exists(config_path):
                return self._get_default_copywriter_rules()

            with open(config_path, "r", encoding="utf-8") as f:
                self._copywriter_rules = yaml.safe_load(f) or {}

        return self._copywriter_rules

    def load_illustrator_rules(self) -> Dict[str, Any]:
        """
        加载插画师 Agent (Illustrator) 的规则配置（config/illustrator_rules.yaml）

        Returns:
            Dict: 包含视觉风格定义和生成配置等的配置字典
        """
        if self._illustrator_rules is None:
            # ✅ 支持通过环境变量指定规则文件（方便测试/多环境切换）
            # 用法示例：
            #   ILLUSTRATOR_RULES_PATH=/abs/path/to/illustrator_rules.yaml python3 your_script.py
            env_rules_path = os.getenv("ILLUSTRATOR_RULES_PATH", "").strip()
            if env_rules_path:
                config_path = env_rules_path
            else:
                config_path = os.path.join(self.config_dir, "illustrator_rules.yaml")

            if not os.path.exists(config_path):
                return self._get_default_illustrator_rules()

            with open(config_path, "r", encoding="utf-8") as f:
                self._illustrator_rules = yaml.safe_load(f) or {}

        return self._illustrator_rules

    def _get_default_illustrator_rules(self) -> Dict[str, Any]:
        """获取默认的 Illustrator 规则配置（当 config 文件不存在时使用）"""
        return {
            "visual_style": {
                "base_env": "Top-down view, warm sand-colored dotted paper texture background, soft lighting, high resolution, cozy atmosphere.",
                "art_style": "Cozy sketchnote style, hand-drawn illustrations using marker outlines and colored pencil shading. Slightly irregular lines.",
                "text_instruction": "The image MUST feature the following text written clearly and legibly in Chinese characters directly on the paper. The text should look like neat handwriting. Do not use pseudo-text. Ensure exact character reproduction.",
            },
            "generation_config": {
                "max_pages": 5,
                "aspect_ratio": "3:4",
            },
        }

    def _get_default_copywriter_rules(self) -> Dict[str, Any]:
        """获取默认的 Copywriter 规则配置（当 config 文件不存在时使用）"""
        return {
            "model_config": {
                "model_name": "gemini-1.5-pro",
                "temperature": 0.7,
                "max_output_tokens": 2048,
            },
            "rag_config": {
                "max_references": 3,
                "min_similarity_score": 0.5,
            },
        }

    def _get_default_analyst_rules(self) -> Dict[str, Any]:
        """获取默认的 Analyst 规则配置（当 config 文件不存在时使用）"""
        return {
            "model_config": {
                "model_name": "gemini-3-pro",
                "temperature": 0.4,
                "max_output_tokens": 2048,
                "batch_size": 1,
            },
            "system_prompt": "",
        }
    
    def _get_default_scout_rules(self) -> Dict[str, Any]:
        """获取默认的抓取规则配置"""
        return {
            "crawl_behavior": {
                "headless_mode": True,
                "scroll_count": 5,
                "request_delay": 2.0,
                "max_notes_to_crawl": 20
            },
            "data_filters": {
                "min_content_length": 20,
                "min_likes": 20,
                "max_days_old": 90
            },
            "blacklist_keywords": [
                "招聘",
                "兼职",
                "刷单",
                "广告",
                "无货源"
            ]
        }
