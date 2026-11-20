"""Generate the public, fictional reading-page sample: python preview/build_web.py."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from web_ui import render_page


PAYLOAD = {
    'updated_at': '2026-11-20T12:32:00+08:00',
    'timezone': 'Asia/Shanghai',
    'articles': [
        {'id': 'demo-suspension', 'title': '主动悬架如何在舒适性与车身姿态之间取得平衡',
         'summary': '车辆驶过连续起伏路面时，主动悬架通过传感器感知车身运动，再调节执行机构，减轻振动对乘坐体验的影响。在转弯和制动等工况下，控制系统还需要兼顾车身姿态与轮胎接地表现。执行机构的响应速度、能耗以及整车标定，都会影响最终效果。',
         'source': '示例 · 悬架技术资料', 'link': 'https://en.wikipedia.org/wiki/Active_suspension',
         'category': '底盘技术', 'published': '2026-11-20T09:40:00+08:00', 'seen_at': '2026-11-20T12:30:00+08:00', 'date': '2026-11-20'},
        {'id': 'demo-battery', 'title': '快充时，电池热管理关注的不只是最高温度',
         'summary': '快充会增加电池发热，热管理系统需要在充电速度、温度均匀性与电芯寿命之间取得平衡。液冷回路、温度传感器与控制策略共同影响散热表现。不同电芯之间的温差，也会影响整组电池的使用状态。设计人员需要结合环境温度、车辆工况和充电功率，确定合适的控制策略。对于低温环境，还可能需要在充电前进行预热。系统既要及时带走热量，也要尽量降低水泵、压缩机等部件的能量消耗。测试通常会覆盖高温、低温和连续充电等条件，以评估不同场景下的温度变化。相关资料进一步介绍了电池包的结构及其与整车热管理系统的连接方式。',
         'source': '示例 · 电池技术资料', 'link': 'https://en.wikipedia.org/wiki/Electric_vehicle_battery',
         'category': '汽车新技术', 'published': '2026-11-20T02:15:00Z', 'seen_at': '2026-11-20T12:30:00+08:00', 'date': '2026-11-20'},
        {'id': 'demo-braking', 'title': '线控制动：从踏板输入到制动力分配',
         'summary': '线控制动将驾驶员的制动意图转化为电子信号，再由执行机构建立制动力。对于电动车，系统还需协调能量回收与摩擦制动，使踏板感受保持连续。系统设计需要考虑冗余、电源故障与不同路面条件下的安全表现。',
         'source': '示例 · 制动技术资料', 'link': 'https://en.wikipedia.org/wiki/Brake-by-wire',
         'category': '底盘技术', 'published': '2026-11-19T14:20:00+08:00', 'seen_at': '2026-11-19T16:30:00+08:00', 'date': '2026-11-19'},
        {'id': 'demo-grid', 'title': 'V2G & 双向充电：车辆如何参与电网调节',
         'summary': '', 'source': '示例 · 双向充电资料', 'link': 'https://en.wikipedia.org/wiki/Vehicle-to-grid',
         'category': '汽车新技术', 'published': None, 'seen_at': '2026-11-19T12:30:00+08:00', 'date': '2026-11-19'},
        {'id': 'demo-invalid', 'title': '转向系统资料更新：一条缺失有效原文链接的示例',
         'summary': '本条用于展示原文链接不可用时的阅读状态。标题和摘要仍保留，页面不提供无效的跳转入口。',
         'source': '示例 · 转向技术资料', 'link': 'javascript:void(0)',
         'category': '底盘技术', 'published': '2026-11-18T11:00:00+08:00', 'seen_at': '2026-11-18T12:30:00+08:00', 'date': '2026-11-18'},
    ],
}


if __name__ == '__main__':
    target = Path(__file__).with_name('web.html')
    target.write_text(render_page(PAYLOAD), encoding='utf-8')
    print(f'Generated {target} (fictional public sample; no network requests)')
