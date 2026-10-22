// 界面上的图标统一用 Lucide，以语义别名导出，不手绘 SVG（DESIGN.md「The Library Icon Rule」；品牌标志是例外，见文件末尾）。
export {
  ArrowRightLeft as MicSwitchIcon,
  ChevronDown as CollapseIcon,
  ChevronLeft as BackIcon,
  Mic as MicIcon,
  MicOff as MicOffIcon,
  Phone as PhoneIcon,
  PhoneOff as HangUpIcon,
  Shirt as OutfitIcon,
} from "lucide-react";

// 她在洗澡没接到的电话（GLOSSARY.md「洗澡」）：对话事件的分隔线。
export { PhoneMissed as MissedCallIcon } from "lucide-react";

// 她结束了对话（GLOSSARY.md「已结束的对话」）：对话事件的分隔线和换掉输入栏的结束卡。
export { MessageCircleOff as EndedIcon } from "lucide-react";

// 记忆页、设置页用的图标（ticket 06）。
export { Trash2 as TrashIcon } from "lucide-react";

// 看板长列表底下的「展开全部」「收起」。
export { ChevronDown as ShowMoreIcon, ChevronUp as ShowLessIcon } from "lucide-react";

// 后台导航用的图标。
export {
  LayoutDashboard as DashboardIcon,
  Megaphone as AnnouncementsIcon,
  SlidersHorizontal as ConfigIcon,
  Users as UsersIcon,
} from "lucide-react";

// 发送键和更多菜单。
export { ArrowUp as SendIcon, Ellipsis as MoreIcon } from "lucide-react";

// 失败提示（components/app/FailureNotice）。
export { CircleAlert as FailureIcon } from "lucide-react";

// 设置页「已绑定」的提示和它的关闭键（shadcn-redesign 18）。
export { CircleCheck as SuccessIcon, X as CloseIcon } from "lucide-react";

// ASMR 模式通话（对话页首更多菜单）。
export { AudioLines as AmbienceIcon, Moon as AsmrIcon } from "lucide-react";

// 信箱（来信）。
export { Mail as LettersIcon } from "lucide-react";

// 日记：导航上的日记本；合上时的锁；她夹起来的那一页。
export { BookLock as DiaryIcon, Lock as DiaryClosedIcon, Paperclip as DiarySecretIcon } from "lucide-react";

// 纪念品柜（GLOSSARY.md「纪念品」）：导航、对话里的礼物卡、补图的按钮。
export { Archive as KeepsakesIcon, Gift as KeepsakeGivenIcon, ImagePlus as FillImagesIcon } from "lucide-react";

// 「一起专心」的番茄钟（对话页更多菜单里打开，页首下方那条计时条）：闹钟、暂停、继续；
// 结束用已有的 CloseIcon。
export { AlarmClock as FocusIcon, Pause as PauseFocusIcon, Play as ResumeFocusIcon } from "lucide-react";

// 「联网」（对话页更多菜单里那一项，GLOSSARY.md「联网」）：一个地球。
export { Globe as WebIcon } from "lucide-react";

// 日历页（导航的「日历」、翻月、制作人自己的生日、和某位偶像的纪念日、答应她的事）。
export {
  Cake as CakeIcon,
  CalendarDays as CalendarIcon,
  CalendarHeart as AnniversaryIcon,
  HeartHandshake as PromiseIcon,
} from "lucide-react";

// 对话列表页首的公告铃铛（GLOSSARY.md「公告」）；后台导航的公告用 Megaphone。
export { Bell as AnnouncementsBellIcon } from "lucide-react";

// 对话列表页首铃铛旁边的服务状态（GLOSSARY.md「服务状态」）。
export { Activity as ServiceStatusIcon } from "lucide-react";

// 对话列表的搜索框；通话条右端「回到通话 ›」的箭头。
export { ChevronRight as ForwardIcon, Search as SearchIcon } from "lucide-react";

// 对话列表搜索框右边的筛选按钮；置顶那一行的图钉，也是行菜单里「置顶」的图标。
export { ListFilter as FilterIcon, Pin as PinIcon } from "lucide-react";

// 对话列表行菜单里的「取消置顶」「在列表中隐藏」「在列表中显示」。
export { Eye as ShowIcon, EyeOff as HideIcon, PinOff as UnpinIcon } from "lucide-react";

// 对话页更多菜单里的「新对话」「历史对话」（换衣装用 OutfitIcon）；历史对话底部只读卡的图标（GLOSSARY.md「历史对话」）。
export { History as PastConversationsIcon, SquarePen as NewConversationIcon } from "lucide-react";

// 后台用户列表可以排序的表头：按它从多到少排着的，和还没按它排的（shadcn-redesign 22）。
export { ArrowDown as SortedIcon, ArrowUpDown as SortableIcon } from "lucide-react";

// 通话层右上角的「只看文字」胶囊。
export { Captions as TextOnlyIcon } from "lucide-react";

// 导航栏（电脑）和 dock（手机）的三项。
export { Gift as GiftIcon, MessageCircle as ConversationsIcon, Settings as SettingsIcon } from "lucide-react";

// 礼物页：抽奖台的「抽一次」和抽到稀有礼物时插画角上的闪光；我的礼物的四个类别筛选；
// 重置卡确认框里「现在的档位 → 信任」的箭头。
export { Sparkles as DrawIcon, Sparkles as RareIcon } from "lucide-react";
export { ArrowRight as BecomesIcon } from "lucide-react";
export { Cookie as FoodIcon, CupSoda as DrinkIcon, Flower2 as FlowerIcon, Gem as TrinketIcon } from "lucide-react";

// 设置一级的十类（档案、记忆、统计、通话用 PhoneIcon、搭话、外观、自带密钥、登录与数据、数据包、关于）和「关于」上面的「赞赏」；「关于」里新标签页打开的链接行尾的外链图标。
export {
  BookHeart as MemoriesIcon,
  NotebookPen as ExperiencesIcon,
  HandHeart as SponsorIcon,
  ChartColumn as StatsIcon,
  CircleUser as ProfileIcon,
  ExternalLink as ExternalLinkIcon,
  Info as AboutIcon,
  KeyRound as OwnKeysIcon,
  Palette as AppearanceIcon,
  LogIn as SignInIcon,
  MessageCircleHeart as ReachOutsIcon,
  Package as DataPackageIcon,
  ToggleRight as FeaturesIcon,
} from "lucide-react";

// 对话页首的好感度标识（GLOSSARY.md「好感度」「冷却」）：五档心形和冷却中的沙漏；
// 回合改变了好感度时，心形上、变化胶囊里和通话层状态行里的升降小箭头。
export {
  ArrowDown as AffinityDownIcon,
  ArrowUp as AffinityUpIcon,
  Heart as AffinityHeartIcon,
  HeartCrack as AffinityDistantIcon,
  HeartOff as AffinityColdIcon,
  Hourglass as CooldownIcon,
} from "lucide-react";

// 她发来的照片（GLOSSARY.md「照片」）：对话记录里照片生成中的占位、对话列表和历史对话面板里最后一句前的相机，
// 没发出来时的说明，和这一小时的照片用完时她那条回复后面的胶囊。
export { Camera as PhotoIcon, CameraOff as PhotosUsedUpIcon, ImageOff as PhotoFailedIcon } from "lucide-react";

// 导航栏底部（电脑）的 GitHub 仓库链接；品牌标志 Lucide 不收，是 The Library Icon Rule 的例外。
export { GitHubIcon } from "./GitHubIcon";
