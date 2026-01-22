#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视觉策划台 (Visual Planning Studio)
交互式工具，用于生成和审核视觉策划方案（提示词）
"""

import os
import sys
import time
import locale
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

# 强制设置 UTF-8 编码环境
if sys.platform != 'win32':
    # 非 Windows 系统，设置环境变量
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    # 尝试设置 locale
    try:
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    except:
        try:
            locale.setlocale(locale.LC_ALL, 'C.UTF-8')
        except:
            try:
                locale.setlocale(locale.LC_ALL, 'zh_CN.UTF-8')
            except:
                pass  # 如果都失败，继续执行

# 确保标准输入输出使用 UTF-8
if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8')
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from skills.illustrator import IllustratorSkill


def get_user_text() -> str:
    """获取用户文案（支持从文件读取或交互式输入）"""
    print("=" * 80)
    print("🎨 视觉策划台 (Visual Planning Studio)")
    print("=" * 80)
    print("\n请选择输入方式：")
    print("  1. 从文件读取（推荐，避免编码问题）")
    print("  2. 交互式输入（粘贴文案后输入 END 结束）")
    
    choice = input("\n请选择 (1/2，直接回车默认为2): ").strip()
    
    # 方式1: 从文件读取
    if choice == "1":
        file_path = input("\n请输入文案文件路径: ").strip()
        if not file_path:
            print("❌ 文件路径为空")
            sys.exit(1)
        
        try:
            file_path = Path(file_path).expanduser().resolve()
            if not file_path.exists():
                print(f"❌ 文件不存在: {file_path}")
                sys.exit(1)
            
            # 尝试多种编码读取
            encodings = ['utf-8', 'gbk', 'gb2312', 'utf-16']
            text = None
            for encoding in encodings:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        text = f.read().strip()
                    print(f"✅ 成功从文件读取（编码: {encoding}）")
                    break
                except UnicodeDecodeError:
                    continue
                except Exception as e:
                    print(f"⚠️  使用 {encoding} 读取失败: {e}")
                    continue
            
            if text is None:
                print("❌ 无法读取文件，尝试了多种编码都失败")
                sys.exit(1)
            
            if not text:
                print("❌ 文件内容为空")
                sys.exit(1)
            
            return text
            
        except Exception as e:
            print(f"❌ 读取文件失败: {e}")
            sys.exit(1)
    
    # 方式2: 交互式输入
    print("\n" + "-" * 80)
    print("💡 提示：")
    print("  1. 请在下面的提示符后粘贴您的文案内容")
    print("  2. 输入完成后，在新的一行输入 'END' 并按回车结束")
    print("  3. 如果遇到编码问题，建议使用方式1（从文件读取）")
    print("-" * 80)
    print("\n请粘贴您的文案内容：")
    
    # 尝试设置终端编码为 UTF-8（如果可能）
    try:
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    except:
        try:
            locale.setlocale(locale.LC_ALL, 'C.UTF-8')
        except:
            pass  # 如果设置失败，继续执行
    
    lines = []
    try:
        while True:
            try:
                line = input()
                if line.strip().upper() == "END":
                    break
                lines.append(line)
            except UnicodeDecodeError as e:
                # 处理编码错误
                print(f"\n⚠️  编码错误：{e}")
                print("💡 建议：")
                print("   1. 退出程序（Ctrl+C）")
                print("   2. 将文案保存到文本文件（UTF-8 编码）")
                print("   3. 重新运行程序，选择方式1（从文件读取）")
                sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户取消操作")
        sys.exit(0)
    except EOFError:
        # 处理 Ctrl+D (Unix) 或 Ctrl+Z (Windows)
        pass
    except UnicodeDecodeError as e:
        # 处理整体编码错误
        print(f"\n⚠️  编码错误：{e}")
        print("💡 建议：")
        print("   1. 将文案保存到文本文件（UTF-8 编码）")
        print("   2. 重新运行程序，选择方式1（从文件读取）")
        sys.exit(1)
    
    text = "\n".join(lines).strip()
    
    if not text:
        print("❌ 文案内容为空，程序退出")
        sys.exit(1)
    
    return text


def display_visual_plan(plans: List[Dict[str, Any]]) -> None:
    """在终端清晰打印视觉策划方案"""
    print("\n" + "=" * 80)
    print("📋 视觉策划方案")
    print("=" * 80)
    
    for plan in plans:
        page_index = plan.get("page_index", 0)
        scene_description = plan.get("scene_description", "")
        image_prompt = plan.get("image_prompt", "")
        text_content = plan.get("text_content", "")
        layout_suggestion = plan.get("layout_suggestion", "")
        
        print(f"\n{'─' * 80}")
        print(f"📄 第 {page_index} 页")
        print(f"{'─' * 80}")
        
        print(f"\n🎨 画面描述（中文）：")
        print(f"   {scene_description}")
        
        print(f"\n✍️  文字内容（原文）：")
        # 如果文字内容较长，适当换行显示
        text_lines = text_content.split('\n')
        for line in text_lines:
            print(f"   {line}")
        
        print(f"\n🎯 排版建议：")
        print(f"   {layout_suggestion}")
        
        print(f"\n📝 英文绘画提示词（可用于 Midjourney/Imagen）：")
        print(f"{'─' * 80}")
        print(image_prompt)
        print(f"{'─' * 80}")


def save_visual_plan_to_markdown(plans: List[Dict[str, Any]], original_text: str) -> str:
    """将视觉策划方案保存为 Markdown 文件"""
    # 创建输出目录
    output_dir = Path("output/prompts")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"visual_plan_{timestamp}.md"
    filepath = output_dir / filename
    
    # 构建 Markdown 内容
    md_content = f"""# 视觉策划方案

**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**总页数**: {len(plans)}

---

## 原始文案

```
{original_text}
```

---

"""
    
    # 为每一页生成内容
    for plan in plans:
        page_index = plan.get("page_index", 0)
        scene_description = plan.get("scene_description", "")
        image_prompt = plan.get("image_prompt", "")
        text_content = plan.get("text_content", "")
        layout_suggestion = plan.get("layout_suggestion", "")
        
        md_content += f"""## 第 {page_index} 页

### 🎨 画面描述（中文）

{scene_description}

### ✍️ 文字内容（原文）

{text_content}

### 🎯 排版建议

{layout_suggestion}

### 📝 英文绘画提示词

```
{image_prompt}
```

**使用说明**：
- 可直接复制上述提示词到 Midjourney 或 Imagen 中使用
- 提示词已包含所有必需的视觉规范关键词

---

"""
    
    # 保存文件
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md_content)
        return str(filepath)
    except Exception as e:
        raise RuntimeError(f"保存 Markdown 文件失败: {e}")


def visual_planning_phase(illustrator: IllustratorSkill, text: str) -> List[Dict[str, Any]]:
    """视觉策划阶段（带反馈循环）"""
    print("\n" + "=" * 80)
    print("📋 视觉策划阶段")
    print("=" * 80)
    
    plans = None
    max_retries = 5  # 增加最大重试次数，允许多次反馈优化
    retry_count = 0
    last_feedback = None  # 保存最后一次反馈
    
    while retry_count < max_retries:
        try:
            # 调用 Illustrator 生成视觉策划方案
            if plans is None:
                print("\n🌐 正在调用 Gemini 生成视觉策划方案...")
                plans = illustrator.plan_visual_prompts(text, max_retries=3)
            else:
                # 如果有反馈，显示优化提示
                if last_feedback:
                    print(f"\n💡 基于您的反馈：【{last_feedback}】进行了优化")
                    print("🔄 正在根据反馈重新生成视觉策划方案...")
                else:
                    print("\n🔄 正在重新生成视觉策划方案...")
                plans = illustrator.plan_visual_prompts(text, max_retries=3)
            
            # 显示方案
            display_visual_plan(plans)
            
            # 保存为 Markdown
            try:
                md_filepath = save_visual_plan_to_markdown(plans, text)
                print(f"\n💾 方案已保存为 Markdown 文件：{md_filepath}")
                print(f"💡 您可以直接打开该文件，复制提示词到 Midjourney/Imagen 中使用")
            except Exception as e:
                print(f"\n⚠️  保存 Markdown 文件失败：{e}")
            
            # 询问用户反馈（审核循环）
            print("\n" + "=" * 80)
            print("💡 请选择：")
            print("  - 输入 'y' = 满意，确认方案")
            print("  - 输入 'n' = 提意见并重做")
            print("  - 输入 'quit' = 退出程序")
            
            user_input = input("\n请选择 (y=满意 / n=提意见并重做 / quit=退出): ").strip().lower()
            
            if user_input == "y":
                print("\n✅ 方案已确认！")
                break
            elif user_input == "n":
                # 处理用户反馈
                print("\n📝 请告诉我哪里需要调整：")
                print("💡 提示：例如 '太花哨了/要极简风'、'颜色太暗/要明亮一些'、'文字太小/要放大' 等")
                feedback = input("您的反馈: ").strip()
                
                if not feedback:
                    print("⚠️  未输入反馈内容，将跳过反馈记录")
                else:
                    # 存储反馈到数据库
                    try:
                        success = illustrator.db.log_feedback(
                            work_id=0,  # work_id=0 表示未关联具体作品
                            persona_name="illustrator",
                            rating=1,  # rating=1 代表负反馈，系统需要修正
                            feedback_text=feedback
                        )
                        if success:
                            print(f"✅ 反馈已记录：{feedback}")
                            print("💡 设计师正在根据您的意见重新策划...")
                            last_feedback = feedback  # 保存反馈用于下次显示
                        else:
                            print(f"⚠️  反馈记录失败，但会继续重新生成")
                            last_feedback = feedback  # 即使记录失败，也保存反馈
                    except Exception as e:
                        print(f"⚠️  反馈记录出错：{e}，但会继续重新生成")
                        last_feedback = feedback  # 即使出错，也保存反馈
                
                # 重新生成方案（带着新记忆）
                plans = None  # 重置，下一轮会重新生成
                retry_count += 1
                if retry_count >= max_retries:
                    print(f"\n❌ 已达到最大重试次数（{max_retries}），程序退出")
                    print(f"💡 您的反馈已保存，下次生成时会参考")
                    sys.exit(1)
                print(f"⏳ 等待 2 秒后继续...")
                time.sleep(2)  # 防止过快重试
                continue
            elif user_input == "quit":
                print("\n👋 程序已退出")
                sys.exit(0)
            else:
                print("❌ 无效输入，请输入 'y'、'n' 或 'quit'")
                time.sleep(1)  # 防止过快循环
                continue
                
        except KeyboardInterrupt:
            print("\n\n⚠️  用户取消操作")
            sys.exit(0)
        except Exception as e:
            print(f"\n❌ 生成视觉策划方案失败：{e}")
            import traceback
            traceback.print_exc()
            
            retry_count += 1
            if retry_count >= max_retries:
                print(f"\n❌ 已达到最大重试次数（{max_retries}），程序退出")
                sys.exit(1)
            
            retry = input(f"\n是否重试？(y/n，剩余 {max_retries - retry_count} 次): ").strip().lower()
            if retry != "y":
                print("👋 程序已退出")
                sys.exit(1)
            
            print(f"⏳ 等待 2 秒后重试...")
            time.sleep(2)  # 防止过快重试
    
    if plans is None:
        raise RuntimeError(f"视觉策划方案生成失败，已达到最大重试次数（{max_retries}）")
    
    return plans


def main():
    """主函数：交互式视觉策划台流程"""
    # 加载环境变量
    load_dotenv()
    
    # 初始化 Illustrator Skill
    try:
        illustrator = IllustratorSkill()
        print("✅ Illustrator Skill 初始化成功\n")
    except Exception as e:
        print(f"❌ Illustrator Skill 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    try:
        # 1. 获取用户文案
        text = get_user_text()
        
        # 2. 视觉策划阶段
        plans = visual_planning_phase(illustrator, text)
        
        # 3. 任务完成
        print("\n" + "=" * 80)
        print("🎉 视觉策划完成！")
        print("=" * 80)
        print(f"\n✅ 共生成 {len(plans)} 个视觉策划方案")
        print(f"💡 所有方案已保存在 output/prompts/ 目录中")
        print(f"💡 您可以直接复制提示词到 Midjourney/Imagen 中使用")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  用户取消操作")
    except Exception as e:
        print(f"\n❌ 程序执行失败：{e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
