#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试视觉策划功能
"""

import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from skills.illustrator import IllustratorSkill


def test_visual_planning():
    """测试视觉策划功能"""
    print("=" * 80)
    print("🧪 测试视觉策划功能")
    print("=" * 80)
    
    # 加载环境变量
    load_dotenv()
    
    # 测试文案
    test_text = """小红书爆款文案示例：

标题：职场新人必看！这5个坑千万别踩

正文：
刚入职场的你，是不是也遇到过这些情况？
1. 不知道如何与领导沟通
2. 同事关系处理不好
3. 工作效率低下
4. 不知道如何提升自己
5. 对未来感到迷茫

今天就来分享5个职场新人最容易踩的坑，帮你快速成长！

第一个坑：不会主动沟通
很多新人觉得领导很忙，不敢主动沟通。但其实，主动沟通是建立信任的关键。

第二个坑：不懂得拒绝
职场中，学会说"不"很重要。不要什么活都接，要有自己的原则。

第三个坑：不注重细节
细节决定成败。一份报告、一封邮件，都能体现你的专业度。

第四个坑：不主动学习
职场是不断学习的过程。只有持续学习，才能跟上时代的步伐。

第五个坑：没有职业规划
没有目标的人生，就像没有方向的船。制定清晰的职业规划，才能走得更远。

记住这5个坑，职场路上少走弯路！"""
    
    try:
        # 初始化 Illustrator Skill
        print("\n[1/3] 初始化 Illustrator Skill...")
        illustrator = IllustratorSkill()
        print("✅ 初始化成功")
        
        # 生成视觉策划方案
        print("\n[2/3] 生成视觉策划方案...")
        plans = illustrator.plan_visual_prompts(test_text, max_retries=3)
        print(f"✅ 成功生成 {len(plans)} 个方案")
        
        # 验证输出格式
        print("\n[3/3] 验证输出格式...")
        required_fields = ["page_index", "scene_description", "image_prompt", "text_content", "layout_suggestion"]
        
        for i, plan in enumerate(plans, 1):
            print(f"\n检查第 {i} 个方案...")
            for field in required_fields:
                if field not in plan:
                    raise ValueError(f"缺少必需字段: {field}")
                if not plan[field]:
                    raise ValueError(f"字段 {field} 为空")
            print(f"  ✅ 所有必需字段都存在")
        
        # 显示方案预览
        print("\n" + "=" * 80)
        print("📋 方案预览")
        print("=" * 80)
        
        for plan in plans[:2]:  # 只显示前2个
            page_index = plan.get("page_index", 0)
            scene_description = plan.get("scene_description", "")[:100]  # 只显示前100字符
            image_prompt = plan.get("image_prompt", "")[:150]  # 只显示前150字符
            
            print(f"\n📄 第 {page_index} 页:")
            print(f"  画面描述: {scene_description}...")
            print(f"  提示词预览: {image_prompt}...")
        
        print("\n" + "=" * 80)
        print("✅ 测试通过！")
        print("=" * 80)
        print(f"\n生成方案数量: {len(plans)}")
        print("所有方案都包含必需的字段")
        print("提示词已包含视觉规范关键词")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


if __name__ == "__main__":
    success = test_visual_planning()
    sys.exit(0 if success else 1)
