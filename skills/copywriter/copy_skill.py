"""
Copywriter Skill

核心能力：
- 加载 config/copywriter_rules.yaml
- 配置 Gemini API（GEMINI_API_KEY）
- 根据人设动态加载 system_prompt
- 生成小红书文案

依赖：
- google.generativeai
- core.config_loader.ConfigManager
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, List

import google.generativeai as genai

from core.config_loader import ConfigManager
from core.db_manager import DBManager


class CopywriterSkill:
    def __init__(self, config: Optional[ConfigManager] = None, db: Optional[DBManager] = None):
        # 1) 加载配置
        self.config = config or ConfigManager()
        self.rules: Dict[str, Any] = self.config.load_copywriter_rules()

        model_cfg = (self.rules.get("model_config") or {})
        # 默认使用 latest，避免固定版本在 v1beta 下 404
        self.model_name: str = str(model_cfg.get("model_name", "gemini-1.5-pro-latest"))
        self.temperature: float = float(model_cfg.get("temperature", 0.7))
        self.max_output_tokens: int = int(model_cfg.get("max_output_tokens", 2048))

        # 2) 配置 Gemini API
        api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("未检测到 GEMINI_API_KEY，请先在环境变量中配置 Gemini API Key。")
        
        genai.configure(api_key=api_key)
        
        # 3) 初始化模型和数据库
        self.model = genai.GenerativeModel(self.model_name)
        self.db = db or DBManager()

    def _list_model_names(self, limit: int = 50) -> List[str]:
        """列出当前 Key 可用模型名（用于诊断 404 model not found）。"""
        names: List[str] = []
        try:
            for m in genai.list_models():
                n = getattr(m, "name", "") or ""
                if n:
                    if n.startswith("models/"):
                        n = n.split("/", 1)[1]
                    names.append(n)
                if len(names) >= limit:
                    break
        except Exception:
            return []
        return names

    def _pick_generatecontent_model(self) -> Optional[str]:
        """挑选一个明确支持 generateContent 的模型名（优先 flash/pro）。"""
        try:
            candidates: List[str] = []
            for m in genai.list_models():
                name = getattr(m, "name", "") or ""
                methods = getattr(m, "supported_generation_methods", None) or []
                if "generateContent" not in methods:
                    continue
                if name.startswith("models/"):
                    name = name.split("/", 1)[1]
                if name:
                    candidates.append(name)
            if not candidates:
                return None

            preferred_keywords = [
                "2.0-flash",
                "2.0-pro",
                "1.5-flash",
                "1.5-pro",
                "flash",
                "pro",
            ]
            for kw in preferred_keywords:
                for c in candidates:
                    if kw in c:
                        return c
            return candidates[0]
        except Exception:
            return None

    def _maybe_reinit_model_for_404(self, err: Exception) -> bool:
        """若是 404 model not found，则自动回退模型并重建 GenerativeModel。"""
        msg = str(err)
        if "404" not in msg or "model" not in msg or "not found" not in msg:
            return False
        picked = self._pick_generatecontent_model()
        if not picked or picked == self.model_name:
            available = self._list_model_names(limit=60)
            if available:
                print("📋 当前 Key 可用模型（截断展示）:", ", ".join(available[:30]))
            return False
        old = self.model_name
        self.model_name = picked
        self.model = genai.GenerativeModel(self.model_name)
        print(f"🔁 Copywriter 模型回退：{old} -> {self.model_name}")
        return True

    def _load_persona_prompt(self, persona_name: str) -> str:
        """
        根据人设名称加载对应的 system_prompt.md 文件
        
        Args:
            persona_name: 人设名称，支持：
                - "sales_expert": 实战派销售导师（原老徐人设）
                - "old_xu"、"oldxu"、"老徐": 与 "sales_expert" 指向同一人设
                - "fiona" 或 "executive_assistant": 行政总助 Fiona
        
        Returns:
            str: 人设的 system_prompt 内容
        
        Raises:
            FileNotFoundError: 如果找不到对应的人设文件
        """
        # 路径映射逻辑
        persona_name_lower = persona_name.lower()
        if persona_name_lower in ("fiona", "executive_assistant"):
            persona_dir = "executive_assistant"
        elif persona_name_lower in ("old_xu", "oldxu", "老徐", "sales_expert"):
            # 老徐人设现统一归档到 personas/sales_expert/
            persona_dir = "sales_expert"
        else:
            # 默认使用 sales_expert（老徐人设）
            persona_dir = "sales_expert"
        
        # 构建人设文件路径（相对于项目根目录）
        project_root = Path(__file__).resolve().parents[2]
        persona_path = project_root / "personas" / persona_dir / "system_prompt.md"
        
        # 检查文件是否存在
        if not persona_path.exists():
            available_personas = []
            personas_dir = project_root / "personas"
            if personas_dir.exists():
                for p in personas_dir.iterdir():
                    if p.is_dir() and (p / "system_prompt.md").exists():
                        available_personas.append(p.name)
            
            error_msg = (
                f"❌ 找不到人设文件：{persona_path}\n"
                f"   请求的人设名称：{persona_name}\n"
                f"   映射到目录：{persona_dir}\n"
            )
            if available_personas:
                error_msg += f"   可用的人设目录：{', '.join(available_personas)}\n"
            error_msg += (
                f"   提示：请确认 personas/{persona_dir}/system_prompt.md 文件存在"
            )
            raise FileNotFoundError(error_msg)
        
        # 读取文件内容
        try:
            with open(persona_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                raise ValueError(f"人设文件为空：{persona_path}")
            return content
        except Exception as e:
            raise RuntimeError(f"读取人设文件失败：{persona_path}，错误：{e}") from e

    def _load_knowledge_base(self, persona_name: str) -> str:
        """
        加载人设专属知识库
        
        Args:
            persona_name: 人设名称
        
        Returns:
            str: 知识库内容（所有 .md 和 .txt 文件内容的拼接），如果文件夹为空或不存在则返回空字符串
        """
        # 路径映射逻辑（与 _load_persona_prompt 保持一致）
        persona_name_lower = persona_name.lower()
        if persona_name_lower in ("fiona", "executive_assistant"):
            persona_dir = "executive_assistant"
        elif persona_name_lower in ("old_xu", "oldxu", "老徐", "sales_expert"):
            # 老徐人设现统一归档到 personas/sales_expert/
            persona_dir = "sales_expert"
        else:
            # 默认使用 sales_expert（老徐人设）
            persona_dir = "sales_expert"
        
        # 构建知识库目录路径（相对于项目根目录）
        project_root = Path(__file__).resolve().parents[2]
        knowledge_dir = project_root / "personas" / persona_dir / "knowledge"
        
        # 如果目录不存在，返回空字符串
        if not knowledge_dir.exists() or not knowledge_dir.is_dir():
            return ""
        
        # 收集所有 .md 和 .txt 文件
        knowledge_files = []
        for ext in ("*.md", "*.txt"):
            knowledge_files.extend(knowledge_dir.glob(ext))
        
        # 如果文件夹为空，返回空字符串
        if not knowledge_files:
            return ""
        
        # 读取所有文件内容并拼接
        knowledge_contents = []
        for file_path in sorted(knowledge_files):  # 排序保证一致性
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        # 添加文件名作为分隔标识（可选，便于调试）
                        knowledge_contents.append(f"--- {file_path.name} ---\n{content}")
            except Exception as e:
                # 如果某个文件读取失败，记录警告但继续处理其他文件
                print(f"⚠️ 读取知识库文件失败：{file_path}，错误：{e}")
                continue
        
        # 用双换行符连接所有文件内容
        return "\n\n".join(knowledge_contents)

    def generate_copy(
        self,
        title_formula: str,
        structure_logic: str,
        persona_name: str = "sales_expert",
        additional_context: Optional[str] = None,
    ) -> str:
        """
        生成小红书文案
        
        Args:
            title_formula: 标题公式（来自 Analyst 的分析结果）
            structure_logic: 结构逻辑（来自 Analyst 的分析结果）
            persona_name: 人设名称，默认为 "sales_expert"
                - "sales_expert": 金牌种草官
                - "fiona" 或 "executive_assistant": 行政总助 Fiona
                - "old_xu" 或 "oldxu": 实战派销售导师 老徐
            additional_context: 额外的上下文信息（可选）
        
        Returns:
            str: 生成的文案内容
        
        Raises:
            FileNotFoundError: 如果找不到对应的人设文件
            RuntimeError: 如果 Gemini API 调用失败
        """
        # 1) 加载人设 system_prompt
        system_prompt = self._load_persona_prompt(persona_name)
        
        # 2) 加载人设专属知识库
        knowledge_content = self._load_knowledge_base(persona_name)
        
        # 3) 加载历史反馈（用于避免重复问题）
        recent_feedback = self.db.get_recent_feedback(persona_name, limit=3)
        
        # 4) 构建完整 prompt
        prompt_parts = [
            system_prompt,
            "",
            "【任务】",
            "请根据以下信息生成一篇小红书文案：",
            "",
            f"【标题公式】\n{title_formula}",
            "",
            f"【结构逻辑】\n{structure_logic}",
        ]
        
        # 如果有知识库内容，添加到 prompt 中
        if knowledge_content:
            prompt_parts.extend([
                "",
                "### Reference Knowledge (参考资料库)",
                "请充分利用以下资料中的信息（如产品参数、语料风格）来辅助创作，但不要生硬复制：",
                knowledge_content,
            ])
        
        # 如果有历史反馈，添加到 prompt 末尾（提醒 AI 避免重复问题）
        if recent_feedback:
            feedback_text = "\n".join([f"- {fb}" for fb in recent_feedback])
            prompt_parts.extend([
                "",
                "### ⚠️ Critical User Feedback (用户最近的批评) / 避坑指南",
                "请注意，你之前因为以下问题被用户批评过，本次生成务必规避：",
                feedback_text,
            ])
        
        if additional_context:
            prompt_parts.extend([
                "",
                "【额外上下文】",
                additional_context,
            ])
        
        prompt = "\n".join(prompt_parts)
        
        # 5) 调用 Gemini 生成文案
        try:
            print(f"📝 开始生成文案（人设：{persona_name}，模型：{self.model_name}）")
            response = self.model.generate_content(
                prompt,
                generation_config={
                    "temperature": self.temperature,
                    "max_output_tokens": self.max_output_tokens,
                },
            )
            
            generated_text = (getattr(response, "text", None) or "").strip()
            if not generated_text:
                raise RuntimeError("Gemini 返回空文本")
            
            print(f"✅ 文案生成完成（长度：{len(generated_text)} 字符）")
            
            # 6) 保存生成的文案到数据库（用于后续审核）
            try:
                # 尝试从生成文本中提取标题（简单规则：第一行或前50个字符）
                lines = generated_text.split('\n')
                extracted_title = lines[0].strip()[:50] if lines else generated_text[:50]
                
                work_id = self.db.save_generated_work(
                    persona_name=persona_name,
                    title=extracted_title,
                    content=generated_text,
                    title_formula=title_formula,
                    structure_logic=structure_logic,
                )
                if work_id:
                    print(f"💾 文案已保存到数据库 (work_id: {work_id})")
            except Exception as e:
                # 保存失败不影响返回结果
                print(f"⚠️ 保存文案到数据库失败: {e}")
            
            return generated_text
            
        except Exception as e:
            # 404 model not found：自动挑一个可用模型再重试一次
            if self._maybe_reinit_model_for_404(e):
                try:
                    response = self.model.generate_content(
                        prompt,
                        generation_config={
                            "temperature": self.temperature,
                            "max_output_tokens": self.max_output_tokens,
                        },
                    )
                    generated_text = (getattr(response, "text", None) or "").strip()
                    if not generated_text:
                        raise RuntimeError("Gemini 返回空文本")
                    return generated_text
                except Exception as e2:
                    raise RuntimeError(f"生成文案失败（已回退模型 {self.model_name}）：{e2}") from e2
            raise RuntimeError(f"生成文案失败：{e}") from e
