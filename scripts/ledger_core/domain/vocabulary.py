"""Generic vocabulary and category templates; never user rules."""
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[2]

SKILL_DIR = SCRIPT_DIR.parent



BILL_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

VALID_TYPES = {"expense", "income", "refund", "transfer"}

VALID_RELATIONS = {"refund", "reimbursement", "repayment", "split", "writeoff"}

VALID_SOURCES = {"text", "image", "voice", "manual", "import"}

VALID_METHODS = {"wechat", "alipay", "cash", "card", "bank", "other"}

VALID_ACCOUNT_TYPES = {"cash", "wechat", "alipay", "bank", "credit", "other"}

RANGE_PRESETS = (
    "this-month",
    "last-month",
    "last7",
    "last30",
    "this-quarter",
    "last-quarter",
    "this-year",
    "last-year",
)

REIMBURSE_TAGS = {"报销", "待报销", "对公"}

REIMBURSED_TAGS = {"已报销", "报销已回", "报销到账"}

WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")

METHOD_TO_ACCOUNT_TYPE = {
    "wechat": "wechat",
    "alipay": "alipay",
    "cash": "cash",
    "card": "credit",
    "bank": "bank",
}

ACCOUNT_TYPE_TO_METHOD = {value: key for key, value in METHOD_TO_ACCOUNT_TYPE.items()}

ACCOUNT_TYPE_LABELS = {
    "cash": "现金",
    "wechat": "微信零钱",
    "alipay": "支付宝",
    "bank": "银行卡",
    "credit": "信用卡",
    "other": "其他",
}

ACCOUNT_ALIASES = {
    "微信零钱": "wechat",
    "微信支付": "wechat",
    "微信": "wechat",
    "支付宝余额": "alipay",
    "支付宝": "alipay",
    "余额宝": "alipay",
    "花呗": "alipay",
    "现金账户": "cash",
    "现金": "cash",
    "信用卡": "credit",
    "银行卡": "bank",
    "借记卡": "bank",
    "储蓄卡": "bank",
}

_ACCOUNT_TOKEN = (
    r"(微信零钱|微信支付|微信|支付宝余额|支付宝|余额宝|现金账户|现金|信用卡|银行卡|借记卡|储蓄卡)"
)

TRANSFER_ROUTE_RE = re.compile(
    r"(?:从)?"
    + _ACCOUNT_TOKEN
    # The verb may be followed directly by the connector ("转到"), or an amount
    # may sit between them ("从微信转100到支付宝").
    + r"\s*(?:转账|转到|转给|转入|转|->|→)"
    + r"\s*(?:\d+(?:\.\d{1,2})?\s*元?\s*)?"
    + r"(?:到|至|进|给)?\s*"
    + _ACCOUNT_TOKEN
)

REQUIRED_TX_FIELDS = {
    "id",
    "type",
    "amount",
    "currency",
    "category",
    "occurred_at",
    "source",
    "created_at",
    "updated_at",
}

CATEGORY_KEYWORDS = {
    "咖啡茶饮": ["星巴克", "瑞幸", "咖啡", "拿铁", "美式", "奶茶", "喜茶", "茶饮"],
    "外食": ["饭", "午饭", "晚饭", "早餐", "外卖", "火锅", "餐厅", "美团", "饿了么", "烧腊", "烘焙"],
    "买菜": ["买菜", "蔬菜", "水果", "海鲜", "买虾", "菜市场"],
    "零食": ["零食", "赵一鸣"],
    "加油": ["加油", "中国石化", "中国石油"],
    "停车通行": ["停车", "停车费", "过路费", "高速费"],
    "车辆养护": ["养车", "洗车", "修车", "保养", "途虎"],
    "交通": ["地铁", "公交", "打车", "滴滴", "高铁", "火车", "机票"],
    "服饰": ["衣服", "服饰", "URBAN REVIVO"],
    "超市": ["超市", "百佳汇"],
    "日用百货": ["百货", "日用品", "便利店"],
    "其他购物": ["淘宝", "京东", "拼多多", "数码"],
    "物业": ["房租", "水电", "燃气", "物业", "宽带"],
    "娱乐": ["电影", "游戏", "演出", "KTV", "会员"],
    "医疗": ["医院", "药", "挂号", "体检"],
    "教育": ["课程", "书", "学费", "培训"],
    "红包": ["红包"],
    "人情往来": ["礼物", "请客"],
    "保险": ["保险", "保费"],
    "软件服务": ["软件", "订阅", "DeepSeek", "深度求索"],
    "收入": ["工资", "奖金", "报销", "利息"],
}

KNOWN_MERCHANTS = ["星巴克", "瑞幸", "麦当劳", "肯德基", "喜茶", "奈雪", "盒马"]

FALLBACK_MERCHANTS = ["美团", "饿了么", "京东", "淘宝", "拼多多", "滴滴"]

TYPE_KEYWORDS = {
    "income": ["工资", "奖金", "收入", "利息", "报销到账"],
    "refund": ["退款", "退回", "返现"],
    "transfer": ["还信用卡"],
}

REIMBURSE_INCOME_KEYWORDS = (
    "报销到账",
    "报销回来",
    "报销了",
    "报销打款",
    "报销入账",
    # "打过来/转过来了" is money arriving. The position carries the tense: "公司
    # 打过来" is an arrival, "公司报销" with no arrival word is an expectation.
    "打过来了",
    "打过来",
    "转过来了",
    "转过来",
    "转给我了",
    "打款了",
    "到账了",
)

REIMBURSE_EXPECTED_KEYWORDS = ("报销", "公司报", "要报销", "能报销", "可以报销", "待报销", "事后报销")

REPAYMENT_INCOME_KEYWORDS = (
    "还我了",
    "还给我了",
    "还给我",
    "退给我了",
    "退给我",
    "退回来了",
    "把钱还了",
    "已还清",
    "还清了",
    "打给我了",
    "打给我",
    "转给我",
    "还回来了",
    "还回来",
)

ADVANCE_KEYWORDS = ("垫付", "垫钱", "代付", "代垫", "借出", "借给", "应收")

INCOME_ONLY_CATEGORIES = {"收入", "红包收入"}

VALID_ADVANCE_STATES = ("open", "settled", "written_off")

NO_ITEMIZED_PREFIXES = ("外卖", "打车", "超市", "买菜", "海淘", "网购", "订单")

RED_PACKET_INCOME_KEYWORDS = ("收到红包", "收红包", "红包收到", "领到红包", "抢到红包")

RED_PACKET_EXPENSE_KEYWORDS = ("发红包", "红包给", "给红包", "发了红包", "包了红包")

TRANSFER_VERBS = ("还信用卡", "提现", "转到", "转入", "内部划转", "还款", "还花呗", "还白条")

TYPE_LINE_MAP = {"支出": "expense", "收入": "income", "退款": "refund", "转账": "transfer"}

METHOD_KEYWORDS = {
    "wechat": ["微信支付", "微信"],
    "alipay": ["支付宝", "花呗", "余额宝"],
    "cash": ["现金"],
    "card": ["信用卡", "刷卡"],
    "bank": ["银行卡", "借记卡", "储蓄卡"],
}

ACCOUNT_KEYWORDS = {
    # "微信付的" names a wallet just as clearly as "微信零钱付的". Without the
    # short forms, the account falls back to the learned default and a stated
    # account is silently replaced by habit.
    "微信零钱": ["微信零钱", "微信里", "微信付", "微信买了"],
    "支付宝": ["支付宝余额", "余额宝", "支付宝付", "支付宝买"],
    "现金": ["现金账户", "现金付", "现金买", "现金"],
    "信用卡": ["信用卡"],
}

TAG_KEYWORDS = {
    "报销": ["报销", "对公", "公司垫付"],
    "出差": ["出差", "差旅"],
    "订阅": ["订阅"],
}

TOTAL_HINTS = ("一共", "共", "合计", "总计", "券后", "实付", "满减")

NOISY_MERCHANT_RE = re.compile(r"有限公司|股份有限|分公司|信息科技|集团|销售有限")

MAX_LEARNABLE_PHRASE = 8

MEMORY_TX_GAP = 10

MEMORY_MAX_AGE_DAYS = 7

HABIT_PHRASES = (
    "早饭",
    "早餐",
    "午饭",
    "午餐",
    "晚饭",
    "晚餐",
    "夜宵",
    "地铁",
    "公交",
    "打车",
    "外卖",
    "奶茶",
    "咖啡",
    "停车",
    "加油",
)

DATE_TOKEN_RE = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}月\d{1,2}[日号]?"
    r"|上周[一二三四五六日天]"
    r"|这?[个]?周[一二三四五六日天]"
    r"|今天|昨天|前天"
    r"|(?<!\d)\d{1,2}[/-]\d{1,2}(?!\d)"
)

AMOUNT_RE = re.compile(r"(?<![\d.])(?:¥|￥)?(\d+(?:\.\d+)?)(?:元|块|人民币)?(?![\d.])")

PAID_AMOUNT_RE = re.compile(r"(?:实付(?:金额)?|券后|支付)\s*(?:¥|￥)?(\d+(?:\.\d+)?)")

ORIGINAL_PRICE_RE = re.compile(r"原价\s*(?:¥|￥)?\d+(?:\.\d+)?")

WEEKDAY_ALIASES = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

METHOD_LABELS = {
    "wechat": "微信",
    "alipay": "支付宝",
    "cash": "现金",
    "card": "信用卡",
    "bank": "银行卡",
    "other": "其他",
}

TYPE_LABELS = {"expense": "支出", "income": "收入", "refund": "退款", "transfer": "转账"}

CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
