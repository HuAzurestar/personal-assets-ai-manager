"""Conservative, local semantic vocabulary -- not a general PII classifier.

Unknown text is not evidence of safety. Only these reviewed business words and
classification grammar may cross the boundary. A merchant with an unknown name
therefore becomes its business category, not a guessed public identity. Extend
the vocabulary with regression evidence, never with values learned from bills.
"""

from __future__ import annotations

import re

# Reviewed public platform names, not identities harvested from a ledger.
# Exact matches also disambiguate short merchant names from personal names.
PUBLIC_PLATFORM_WORDS = frozenset("""
淘宝 淘宝网 天猫 京东 京东商城 拼多多 美团 美团外卖 饿了么
""".split())

BUSINESS_WORDS = frozenset("""
个人转账 退还垫款 文具购买 午餐套餐 宴会餐厅 咖啡馆 矿泉水 饮用水
餐饮 美食 早餐 午餐 晚餐 夜宵 聚餐 工作餐 套餐 餐费 饭店 餐厅 餐馆
咖啡 奶茶 茶 饮料 食品 水果 蔬菜 外卖 超市 商场 便利店 文具店 文具
日用品 生活用品 服装 鞋帽 购物 数码 家电 图书 书籍 学费 教育 培训
医疗 医药 药品 医院 挂号 健康 运动 健身 娱乐 电影 游戏 音乐 旅游
住宿 酒店 出行 交通 地铁 公交 铁路 火车 高铁 航空 机票 打车 出租车
加油 充电 停车 通信 话费 网费 水费 电费 燃气 物业 房租 住房
工资 薪资 收入 支出 收款 付款 消费 转账 退款 退货 返还 垫款 报销
借款 还款 贷款 利息 理财 投资 保险 捐赠 红包 礼物 服务 订阅
银行卡 余额支付 现金 微信支付 支付宝 云闪付
日用消耗品 消耗品 耗材 纸巾 卫生纸 洗衣液 洗发水 牙膏 清洁用品
食品生鲜 生鲜 数码家电 居住缴费 教育学习 休闲娱乐 住宿旅游 通信订阅
药店 药房 诊疗 体检 检查费 服饰 衣服 裤子 连衣裙 鞋子 运动鞋
手机 电脑 耳机 网购 电商 网上商城 线上 线下 到店 门店 柜台 POS
线上订单 购买渠道 消费用途
Food Cafe Breakfast Lunch Dinner Meal Transport Travel Shopping Refund
food cafe breakfast lunch dinner meal transport travel shopping refund
""".split()) | PUBLIC_PLATFORM_WORDS

GRAMMAR_WORDS = frozenset("""
根据 依据 结合 参考 仅 只 安全 用途 语义 商户 商品 摘要 描述 信息
从 候选 标签 分类 选择 判断 识别 匹配 建议 归类 属于 对应 支持 表明
包含 显示 体现 涉及 用于 进行 相关 明确 可能 可见 特征 日常 工作日
消费分类 交易类型 交易 经济 活动 场景 类型 行为 内容 业务 项目
不足 不明 无法 确定 时 返回 不得 外发 不 单独 决定 默认 优先
与 和 及 或 为 是 的 中 有 无 此 该 本笔 一笔 这笔 可 不能 应
选择一个标签 根据商户和摘要选择分类 Choose one tag select classify
支付方式 表示 混合 商品用途 不适配
insufficient suggestion category purpose evidence semantic synthetic reason
""".split())

_SPECIFICATION = r"(?:矿泉水|饮用水)\s*\d{1,4}(?:\.\d{1,2})?\s*(?:mL|ml|毫升|L|升)"
_PUNCTUATION = re.compile(r"^[\s,，;；。.!！?？:：、()（）\[\]【】/\-]*$")


def _pattern(grammar: bool) -> re.Pattern:
    words = BUSINESS_WORDS | GRAMMAR_WORDS if grammar else BUSINESS_WORDS
    parts = [re.escape(word) for word in sorted(words, key=lambda word: (-len(word), word))]
    # Don't preserve a known English word as part of an unknown ID/name.
    parts = [rf"(?<![A-Za-z]){word}(?![A-Za-z])" if word.isascii() else word for word in parts]
    return re.compile("|".join([_SPECIFICATION, *parts]))


_BUSINESS = _pattern(False)
_CLASSIFICATION = _pattern(True)


def semantic_text(value: str, *, grammar: bool = False) -> str:
    """Retain safe words, never unknown gaps (including unlabelled identities)."""
    chunks: list[str] = []
    end = 0
    for match in (_CLASSIFICATION if grammar else _BUSINESS).finditer(value):
        gap = value[end:match.start()]
        if chunks and gap:
            chunks.append(gap if _PUNCTUATION.fullmatch(gap) else "，")
        chunks.append(match.group())
        end = match.end()
    return "".join(chunks).strip(" ,，;；。:：")


def is_safe_reason(value: str) -> bool:
    """Unlike inputs, an output with ANY unknown fragment is rejected as a whole."""
    end = 0
    found_business = False
    for match in _CLASSIFICATION.finditer(value):
        if not _PUNCTUATION.fullmatch(value[end:match.start()]):
            return False
        found_business |= bool(_BUSINESS.fullmatch(match.group()))
        end = match.end()
    return found_business and bool(_PUNCTUATION.fullmatch(value[end:]))
