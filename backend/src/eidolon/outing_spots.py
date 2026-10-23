"""Where a day out goes, stop by stop (GLOSSARY.md「约会」): for each place on the map the producer
can invite her to (`city_map.DATE_SPOTS`), and for a day out that names none (`AROUND`), the few
stops it goes through, in order. Each says what it is like there, for her prompt, and is drawn
from tags, for its background where the deployer has image generation, and for the photos taken
there."""

from dataclasses import dataclass

from eidolon.city_map import Place


@dataclass(frozen=True)
class Stop:
    id: str
    name: str
    """What it is called, as the user is shown it."""
    scene: str
    """What it is like there, as she is told it: what there is to see and do."""
    tags: str
    """English tags drawing it, no one in it."""


AROUND = "around"
"""A day out that names no place on the map: around town."""

STOPS: dict[str, tuple[Stop, ...]] = {
    "aquarium": (
        Stop("entrance", "入口大厅", "水族馆的入口大厅，头顶是巨大的鲸鱼骨骼标本，买好票的人们往里走。", "aquarium, lobby, whale skeleton, ticket gate, indoors, soft blue lighting"),
        Stop("jellyfish", "水母隧道", "昏暗的水母展区，一整面墙的水槽里，发着光的水母慢慢飘着，四周很安静。", "aquarium, jellyfish tank, dark room, glowing jellyfish, blue light, indoors"),
        Stop("penguins", "企鹅馆", "企鹅馆，企鹅在岩石上摇摇摆摆地走，偶尔跳进水里游得飞快，玻璃前围着小朋友。", "aquarium, penguin enclosure, rocks, water pool, glass wall, indoors, bright"),
        Stop("shop", "纪念品店", "出口旁的纪念品店，货架上摆满了企鹅和海豹的布偶、钥匙扣、明信片。", "souvenir shop, shelves, plush toys, keychains, aquarium gift shop, indoors, warm lighting"),
    ),
    "amusement": (
        Stop("gate", "乐园大门", "游乐园的大门，彩色的拱门和气球，广播里放着欢快的音乐。", "amusement park, entrance gate, colorful arch, balloons, crowd, daytime"),
        Stop("coaster", "过山车", "过山车的排队区，头顶不时传来尖叫声，轨道高高地绕过天空。", "amusement park, roller coaster, track, queue line, blue sky"),
        Stop("carousel", "旋转木马", "旋转木马在音乐里一圈圈转着，旁边的摊子在卖爆米花和吉事果。", "amusement park, carousel, merry-go-round, popcorn stand, lights"),
        Stop("wheel", "摩天轮", "摩天轮的小车厢里，慢慢升到最高处，能看见整个乐园和远处的街景。", "ferris wheel gondola, interior, window view, city view, sunset sky"),
    ),
    "zoo": (
        Stop("gate", "动物园入口", "上野动物园的入口，地图板前很多人在商量先去看哪儿。", "zoo entrance, gate, signboard map, trees, daytime"),
        Stop("pandas", "熊猫馆", "熊猫馆，熊猫抱着竹子慢吞吞地吃，大家都举着手机。", "zoo, panda enclosure, bamboo, glass window, indoors"),
        Stop("pond", "不忍池旁", "动物园边上的不忍池，有天鹅船在水上慢慢划，岸边有长椅。", "pond, swan boats, park bench, willow trees, lotus leaves, daytime"),
    ),
    "tower": (
        Stop("foot", "塔下", "东京塔脚下，抬头能看到红白的铁塔一直伸到天上。", "tokyo tower, base, looking up, red and white tower, city street"),
        Stop("deck", "展望台", "展望台的大玻璃窗前，整个东京铺在脚下，远处能看到海和山。", "observation deck, large windows, city panorama, tokyo skyline, indoors"),
        Stop("night", "夜景", "天黑以后的展望台，窗外的街灯一片一片亮起来，像星星落在地上。", "observation deck, night view, city lights, window reflection, indoors, night"),
    ),
    "sea": (
        Stop("station", "海边车站", "靠海的小车站，下车就能闻到海风，远处有海浪声。", "seaside train station, platform, ocean view, summer, daytime"),
        Stop("beach", "沙滩", "沙滩上，海浪一下一下地拍上来，留下贝壳和湿湿的沙子。", "beach, ocean, waves, sand, seashells, blue sky"),
        Stop("sunset", "看夕阳", "堤岸上，太阳慢慢沉进海里，天空从橘色变成粉紫色。", "seawall, sunset, ocean, orange sky, horizon, evening"),
    ),
    "asakusa": (
        Stop("gate", "雷门", "浅草雷门，巨大的红灯笼下挤满了拍照的游客。", "kaminarimon, asakusa, giant red lantern, temple gate, crowd, daytime"),
        Stop("street", "仲见世通", "通往寺庙的小商店街，人形烧和仙贝的香味飘过来，两边是卖小玩意儿的铺子。", "nakamise street, asakusa, shopping street, food stalls, lanterns"),
        Stop("temple", "浅草寺", "浅草寺正殿前，香炉冒着烟，大家在求签、许愿。", "sensoji temple, main hall, incense burner, omikuji stand, japanese temple"),
    ),
    "cinema": (
        Stop("lobby", "电影院大厅", "电影院大厅，墙上贴满了新片海报，爆米花的香味很浓。", "cinema lobby, movie posters, popcorn counter, indoors, warm lighting"),
        Stop("seats", "放映厅", "放映厅里，灯慢慢暗下来，大银幕亮起，两个人并排坐着。", "movie theater, seats, big screen, dark room, indoors"),
        Stop("after", "散场后", "散场后的路上，两个人一边走一边聊刚才的电影。", "city street, evening, shop lights, sidewalk"),
    ),
    "cafe": (
        Stop("counter", "点单", "小小的咖啡店，黑板上写着今日推荐，柜台里摆着蛋糕。", "cafe, counter, chalkboard menu, cake showcase, indoors, cozy"),
        Stop("window", "窗边座位", "窗边的座位，阳光照在桌上，两杯饮料和一块蛋糕。", "cafe, window seat, table, coffee cups, cake, sunlight, indoors"),
        Stop("walk", "回去的路上", "从咖啡店出来，沿着安静的小街慢慢走。", "quiet street, residential, trees, afternoon"),
    ),
    "park": (
        Stop("path", "林荫道", "公园的林荫道，阳光从树叶间漏下来，有人在遛狗、慢跑。", "park, tree-lined path, sunlight through leaves, daytime"),
        Stop("lawn", "草坪", "开阔的草坪，铺开野餐垫就能坐下，远处有孩子在放风筝。", "park, lawn, picnic blanket, kites, blue sky"),
        Stop("pond", "池塘边", "池塘边的长椅，水面上有鸭子游过，风轻轻吹着。", "park, pond, bench, ducks, trees, afternoon"),
    ),
    "shopping": (
        Stop("street", "商店街", "热闹的商店街，两边是服装店、杂货店和可丽饼摊。", "shopping street, shops, crepe stand, crowd, daytime, harajuku"),
        Stop("shop", "杂货店", "一家可爱的杂货店，里面摆着发饰、小镜子和香薰蜡烛。", "zakka shop, accessories, hair clips, shelves, indoors, cute"),
        Stop("purikura", "大头贴", "游戏中心的大头贴机里，亮亮的灯光，屏幕上可以画画。", "purikura booth, interior, bright lights, game center"),
    ),
    AROUND: (
        Stop("meet", "碰头", "约好碰头的车站前，人来人往。", "train station, plaza, crowd, daytime"),
        Stop("stroll", "随便走走", "街上，两个人随意地逛着，看到什么就停下来看看。", "city street, shops, sidewalk, afternoon"),
        Stop("rest", "歇一会儿", "街角的长椅，买了两杯饮料坐下来休息。", "street corner, bench, drinks, evening light"),
    ),
}


def stops_of(spot: Place | None) -> tuple[Stop, ...]:
    """The stops of a day out at `spot`, around town if it names none of the map's places."""
    return STOPS.get(spot or AROUND, STOPS[AROUND])
