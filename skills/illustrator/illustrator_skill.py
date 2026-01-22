"""
Illustrator Skill - 提示词生成专家

核心能力：
- 加载 config/illustrator_rules.yaml
- 配置 Gemini API（GEMINI_API_KEY）
- 使用 Gemini Text Model 将文案拆分为3-5个部分，生成高质量的视觉策划方案
- 为每一页生成可在 Midjourney/Imagen 中使用的高质量提示词

依赖：
- google.generativeai
- core.config_loader.ConfigManager
- core.db_manager.DBManager
"""

from __future__ import annotations

import json
import os
import time
import socket
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

# 屏蔽干扰警告
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import google.generativeai as genai

from core.config_loader import ConfigManager
from core.db_manager import DBManager


class IllustratorSkill:
    def __init__(self, config: Optional[ConfigManager] = None):
        # 1) 加载配置
        self.config = config or ConfigManager()
        self.rules: Dict[str, Any] = self.config.load_illustrator_rules()

        # 2) 配置 Gemini API
        api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("未检测到 GEMINI_API_KEY，请先在环境变量中配置 Gemini API Key。")
        
        # 强制使用 REST 协议，规避 gRPC 连接失败导致的长时间阻塞/重试
        genai.configure(api_key=api_key, transport='rest')
        print(f"🔧 Gemini transport=rest (强制使用 REST 协议)")

        # 3) 初始化 Gemini Text Model（延迟选择，避免初始化时卡死）
        # 注意：部分 Key / API 版本下 `gemini-1.5-pro-latest` 可能 404。
        # 这里选择更通用的 flash 作为默认首选，失败时再自动 list_models 回退。
        preferred_model = "gemini-1.5-flash"
        self.text_model_name = preferred_model
        print(f"📦 预设模型: {self.text_model_name}（首次调用时会自动选择可用模型）")
        try:
            self.text_model = genai.GenerativeModel(self.text_model_name)
        except Exception as e:
            # 如果默认模型不可用，立即尝试选择可用模型
            print(f"⚠️  默认模型不可用: {e}，尝试自动选择可用模型...")
            self.text_model_name = self._pick_available_model_safe(preferred_model)
            self.text_model = genai.GenerativeModel(self.text_model_name)
            print(f"✅ 已切换至模型: {self.text_model_name}")
        
        # 4) 获取视觉风格配置
        visual_style = self.rules.get("visual_style", {})
        self.base_env = visual_style.get("base_env", "")
        self.art_style = visual_style.get("art_style", "")
        self.text_instruction = visual_style.get("text_instruction", "")
        
        # 5) 获取生成配置
        gen_config = self.rules.get("generation_config", {})
        self.max_pages = int(gen_config.get("max_pages", 5))
        self.aspect_ratio = str(gen_config.get("aspect_ratio", "3:4"))
        
        # 6) 初始化数据库管理器（用于读取视觉反馈记忆）
        self.db = DBManager()
        
        # 7) 网络预检（可选，不阻塞初始化）
        try:
            self._preflight_network()
        except Exception as e:
            print(f"⚠️  网络预检失败: {e}，将继续尝试（可能需要代理）")

    def _preflight_network(self) -> None:
        """
        预检网络连通性：避免在 API 调用时"长时间重试看起来像卡死"
        """
        host = "generativelanguage.googleapis.com"
        port = 443
        
        # 检查代理配置
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
        if proxy:
            print(f"🌐 检测到代理: {proxy}，跳过直连检查")
            return
        
        # 尝试 DNS 解析
        try:
            socket.gethostbyname(host)
        except socket.gaierror:
            raise RuntimeError(f"DNS 解析失败: {host}")
        
        # 尝试 TCP 连接（非阻塞，快速失败）
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((host, port))
            sock.close()
            if result != 0:
                raise RuntimeError(f"TCP 连接失败: {host}:{port}（可能需要代理）")
        except Exception as e:
            raise RuntimeError(f"网络检查失败: {e}")

    def _list_model_names(self, limit: int = 50, timeout: int = 15) -> List[str]:
        """
        列出当前 Key/Endpoint 下可用的模型名（用于诊断 404 model not found）。
        """
        names: List[str] = []
        try:
            import signal
            
            def timeout_handler(signum, frame):
                raise TimeoutError(f"list_models() 超时（{timeout}秒）")
            
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(timeout)
            
            try:
                for m in genai.list_models():
                    n = getattr(m, "name", "") or ""
                    if n:
                        if n.startswith("models/"):
                            n = n.split("/", 1)[1]
                        names.append(n)
                    if len(names) >= limit:
                        break
            finally:
                signal.alarm(0)
        except TimeoutError:
            print(f"⚠️  list_models() 超时，跳过模型列表获取")
        except Exception:
            # 列表失败不影响主流程
            pass
        return names

    def _pick_generatecontent_model(self) -> Optional[str]:
        """
        选一个明确支持 generateContent 的模型名（优先 flash/pro，再兜底第一个可用）。
        注意：这个方法可能卡住（list_models 没有超时保护），所以不应该在初始化时调用。
        """
        try:
            import signal
            
            def timeout_handler(signum, frame):
                raise TimeoutError("_pick_generatecontent_model() 超时（10秒）")
            
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(10)  # 10秒超时
            
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
                    if len(candidates) >= 20:  # 限制数量，避免卡太久
                        break

                signal.alarm(0)  # 取消超时
                
                if not candidates:
                    return None

                # 常见优先级（新版本可能是 2.0 系列；旧版本可能是 1.5 系列）
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
            finally:
                signal.alarm(0)  # 确保取消超时
        except TimeoutError:
            print(f"⚠️  _pick_generatecontent_model() 超时，跳过")
            return None
        except Exception:
            return None

    def _pick_available_model_safe(self, preferred_model: str) -> str:
        """
        安全地选择一个可用的模型（带超时保护）。
        """
        try:
            picked = self._pick_generatecontent_model()
            if picked:
                return picked
        except Exception:
            pass
        
        # 如果 pick_generatecontent_model 失败，尝试列出所有模型
        available_models = self._list_model_names(limit=30, timeout=15)
        
        # 优先级列表
        priority_models = [
            "gemini-2.0-flash-exp",
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ]
        
        for model in priority_models:
            if model in available_models:
                return model
        
        # 如果优先列表都没有，返回第一个可用的
        if available_models:
            return available_models[0]
        
        # 如果都不可用，返回默认值
        return preferred_model

    def _get_visual_memory(self) -> str:
        """
        从数据库获取最近的视觉反馈记忆（用于指导 Prompt 生成）。
        返回格式化的文本字符串，用于插入到 System Prompt 中。
        """
        try:
            feedback_list = self.db.get_recent_feedback(persona_name="illustrator", limit=5)
            if not feedback_list:
                return "暂无特定偏好"
            
            memory_lines = []
            for fb in feedback_list:
                # 处理返回值：get_recent_feedback 返回 List[str]（纯文本列表）
                if isinstance(fb, str):
                    # 如果是字符串，直接使用
                    if fb.strip():
                        memory_lines.append(f"- 反馈: {fb.strip()}")
                elif isinstance(fb, dict):
                    # 如果是字典，提取字段（兼容处理）
                    rating = fb.get("rating", 0)
                    feedback_text = fb.get("feedback_text", "").strip()
                    if feedback_text:
                        memory_lines.append(f"- 评分: {rating}/5, 反馈: {feedback_text}")
                    elif rating:
                        memory_lines.append(f"- 评分: {rating}/5")
                else:
                    # 其他类型，转为字符串
                    fb_str = str(fb).strip()
                    if fb_str:
                        memory_lines.append(f"- 反馈: {fb_str}")
            
            if memory_lines:
                return "\n".join(memory_lines)
            return "暂无特定偏好"
        except Exception as e:
            print(f"⚠️  获取视觉记忆失败：{e}，将使用空记忆")
            return "暂无特定偏好"

    def plan_visual_prompts(self, full_text: str, max_retries: int = 3) -> List[Dict[str, Any]]:
        """
        视觉策划：将文案拆解为 3-5 张笔记内页，为每一页生成完整的视觉策划方案。
        
        Args:
            full_text: 完整的文案内容
            max_retries: 最大重试次数，默认3次
            
        Returns:
            List[Dict[str, Any]]: 视觉策划方案列表，每个对象包含：
                - page_index: int, 页码（1-5）
                - scene_description: str, 中文画面描述（给人类看，用于审核）
                - image_prompt: str, 英文绘画提示词（包含 detailed style, texture, composition, lighting）
                - text_content: str, 本页必须包含的文字内容（引用原文，不删减）
                - layout_suggestion: str, 排版建议（例如：文字在上半部分，插画在右下角）
        """
        # 获取视觉记忆（用户历史审美偏好）
        memory = self._get_visual_memory()
        
        # 构建 System Prompt（强化视觉规范关键词，动态插入长期记忆）
        system_prompt = f"""你是一位专业的小红书视觉策划总监。

⚠️ 视觉规范（必须严格遵守）：
1. 基础环境关键词（必须包含）：
   - "Top-down view"（俯视角度）
   - "Warm sand-colored dotted paper texture"（暖沙色点阵纸纹理）
   - "Soft studio lighting"（柔和影棚光）
   - "High resolution"（高分辨率）

2. 艺术风格关键词（必须包含）：
   - "Cozy sketchnote style"（温馨手绘笔记风）
   - "Marker pen outlines"（马克笔轮廓）
   - "Colored pencil shading"（彩色铅笔阴影）
   - "Slightly irregular lines"（略微不规则的线条）

3. 品牌元素关键词（必须包含）：
   - "Dark green card with white logo"（深绿色卡片配白色logo）
   - "Black matte envelope"（黑色哑光信封）

4. 文字渲染要求：
   {self.text_instruction}

### ⚠️ 用户历史反馈 (User Preferences)
这是用户过去对方案的修改意见，你必须严格遵守，避免重复犯错：
{memory}

### 任务
请阅读文案，将其拆分为 3-5 个部分（每部分对应一张笔记内页），为每一部分生成一个完整的视觉策划方案。

### 输出格式要求
请输出 JSON 列表，每个对象包含以下字段：
- page_index: 页码（整数，从1开始）
- scene_description: 中文画面描述（给人类看，用于审核，详细描述画面内容）
- image_prompt: 英文绘画提示词（必须包含上述所有关键词，详细描述 style, texture, composition, lighting）
- text_content: 本页必须包含的文字内容（完整引用原文，不删减任何文字）
- layout_suggestion: 排版建议（例如："文字在上半部分，插画在右下角"）

输出为 JSON 列表：[{{"page_index": 1, "scene_description": "...", "image_prompt": "...", "text_content": "...", "layout_suggestion": "..."}}, ...]
请仅输出纯 JSON 数据，不要包含 Markdown 标记或其他说明文字。
确保 image_prompt 中包含所有必需的视觉规范关键词。

### 重要提示
1. 必须严格遵守视觉规范中的所有关键词
2. **必须严格遵守用户历史反馈中的要求，避免重复犯错**
3. 如果用户反馈中提到"太暗了"，必须在 Prompt 中加入 'bright lighting, high key exposure' 等描述
4. 如果用户反馈中提到"颜色不对"，必须相应调整颜色描述
5. 确保所有生成的 Prompt 都符合用户的审美偏好
"""

        # 构建完整提示词
        prompt = f"{system_prompt}\n\n文案内容：\n{full_text}"
        
        last_error: Optional[Exception] = None

        attempt = 0
        model_switched = False  # 标记是否已切换过模型
        
        while attempt < max_retries:
            try:
                print(f"🌐 调用 Gemini 生成视觉策划方案...（model={self.text_model_name}, 尝试 {attempt+1}/{max_retries}）")
                print("📡 正在发送请求到 Gemini API...")
                print("⏳ 请耐心等待，通常需要 15-60 秒（取决于网络和 Prompt 长度）...")
                t0 = time.time()
                
                # 增加超时时间到 120 秒，避免大 Prompt 或网络慢时超时
                resp = self.text_model.generate_content(
                    prompt,
                    generation_config={
                        "temperature": 0.7,
                        "max_output_tokens": 4096,
                    },
                    request_options={"timeout": 120},  # 120秒超时（增加到120秒，避免大提示词超时）
                )
                
                dt = time.time() - t0
                print(f"📥 Gemini 返回完成（耗时={dt:.1f}s）")
                
                raw_text = (getattr(resp, "text", None) or "").strip()
                if not raw_text:
                    raise RuntimeError("Gemini 返回空文本")
                
                # 尝试解析 JSON
                try:
                    # 尝试直接解析
                    result_list = json.loads(raw_text)
                except Exception:
                    # 尝试截取第一个 JSON 数组（防止模型前后夹带说明）
                    start = raw_text.find("[")
                    end = raw_text.rfind("]")
                    if start != -1 and end != -1 and end > start:
                        result_list = json.loads(raw_text[start : end + 1])
                    else:
                        raise ValueError("无法解析 Gemini 返回的 JSON 格式")
                
                if not isinstance(result_list, list):
                    raise ValueError(f"解析结果不是 JSON 列表：{type(result_list).__name__}")
                
                # 验证每个元素的结构
                required_fields = ["page_index", "scene_description", "image_prompt", "text_content", "layout_suggestion"]
                for i, item in enumerate(result_list):
                    if not isinstance(item, dict):
                        raise ValueError(f"列表中的第 {i+1} 个元素必须是字典")
                    for field in required_fields:
                        if field not in item:
                            raise ValueError(f"列表中的第 {i+1} 个元素缺少必需字段: {field}")
                
                print(f"✅ 成功生成 {len(result_list)} 个视觉策划方案")
                return result_list
                
            except Exception as e:
                last_error = e
                error_msg = str(e)
                
                # 检查是否是 404 模型不可用错误，如果是则尝试自动切换模型
                if "404" in error_msg and ("model" in error_msg.lower() or "not found" in error_msg.lower()):
                    if not model_switched:  # 只切换一次，避免无限循环
                        print(f"⚠️  模型 {self.text_model_name} 不可用，尝试自动切换模型...")
                        new_model = self._pick_available_model_safe("gemini-1.5-flash")
                        if new_model and new_model != self.text_model_name:
                            try:
                                self.text_model_name = new_model
                                self.text_model = genai.GenerativeModel(self.text_model_name)
                                print(f"✅ 已切换至模型: {self.text_model_name}")
                                model_switched = True
                                # 切换模型后，重置重试计数（给新模型一次机会）
                                attempt = 0
                                wait_time = 2
                                print(f"⏳ 等待 {wait_time} 秒后使用新模型重试...")
                                time.sleep(wait_time)
                                continue
                            except Exception as switch_err:
                                print(f"❌ 切换模型失败: {switch_err}")
                        else:
                            print(f"❌ 无法找到可用模型（当前: {self.text_model_name}）")
                    else:
                        print(f"❌ 已切换过模型但仍失败，停止重试")
                
                # 不打印完整错误（可能包含大数据），只打印关键信息
                error_msg_short = str(e)
                if len(error_msg_short) > 200:
                    error_msg_short = error_msg_short[:200] + "...（错误信息过长已省略）"
                print(f"❌ 生成视觉策划方案失败（尝试 {attempt+1}/{max_retries}）：{error_msg_short}")
                
                attempt += 1
                if attempt < max_retries:
                    # 强制等待 2 秒后重试（防止过快重试导致卡死）
                    wait_time = 2
                    print(f"⏳ 等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ 达到最大重试次数（{max_retries}），生成视觉策划方案失败")
                    last_msg = str(last_error) if last_error else "未知错误"
                    if len(last_msg) > 800:
                        last_msg = last_msg[:800] + "...（错误信息过长已省略）"
                    raise RuntimeError(
                        "生成视觉策划方案失败：已达到最大重试次数。\n"
                        f"最后一次错误：{last_msg}\n"
                        "建议：检查代理/网络是否可访问 Google Gemini；必要时在侧边栏配置 HTTPS_PROXY/HTTP_PROXY。"
                    )
        
        # 所有重试都失败（理论兜底）
        last_msg = str(last_error) if last_error else "未知错误"
        raise RuntimeError(f"生成视觉策划方案失败：{last_msg}")
