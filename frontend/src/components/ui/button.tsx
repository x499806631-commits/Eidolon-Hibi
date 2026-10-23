import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const buttonVariants = cva(
  // eidolon: 加 aria-disabled:opacity-50。focusableWhenDisabled 的按钮（对话页首的电话键：通话结束后焦点要能落回它上面）
  // 不带 disabled 属性、只有 aria-disabled，原来的 disabled: 样式管不到它，看起来就不像禁用。
  "group/button inline-flex shrink-0 items-center justify-center rounded-lg border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap transition-all outline-none select-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:not-aria-[haspopup]:translate-y-px disabled:pointer-events-none disabled:opacity-50 aria-disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/80",
        outline:
          "border-border bg-background hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50",
        // eidolon: 悬停色原来是任意值里的 color-mix(…var(--secondary)…)；任意值里不写颜色（frontend/AGENTS.md），改用 /80。
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80 aria-expanded:bg-secondary aria-expanded:text-secondary-foreground",
        ghost:
          "hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground dark:hover:bg-muted/50",
        // eidolon: 原来是淡红底红字；DESIGN.md「Buttons」要挂断红实底、白字（白字的对比度在 themeContrast.test.ts 里）。
        destructive:
          "bg-destructive text-white hover:bg-destructive/90 focus-visible:border-destructive/40 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40",
        link: "text-primary underline-offset-4 hover:underline",
        // eidolon: 她颜色的圆按钮（发送键等，DESIGN.md「button.bg / button.fg」），要在 herVars() 的子树里用。
        her: "rounded-full bg-her-button text-her-button-fg hover:bg-her-button/80",
        // eidolon: 通话层（强制深色、底是她的头像或立绘）上的几种胶囊和圆键（DESIGN.md「Call layer」）。焦点环一律 60% 白：
        // 底色随她的颜色和立绘变，--ring 在上面不一定看得见。
        // 毛玻璃胶囊：「只看文字」、结束卡的「回到对话」。按下（aria-pressed）时白底黑字。
        "call-glass":
          "rounded-full bg-white/15 font-normal text-white hover:bg-white/25 focus-visible:border-transparent focus-visible:ring-white/60 aria-pressed:bg-white aria-pressed:text-black",
        // 白底黑字的主操作：结束卡上的重拨、「去设置自带密钥」。
        "call-solid":
          "rounded-full bg-white text-black hover:bg-white/90 focus-visible:border-transparent focus-visible:ring-white/60",
        // 底栏的圆形图标键（静音、切换麦克风）：按下时白底黑图标；呼叫中置灰，但仍接得到悬停，好显示提示。
        "call-round":
          "rounded-full text-white hover:bg-white/10 focus-visible:border-transparent focus-visible:ring-white/60 aria-pressed:bg-white aria-pressed:text-black disabled:pointer-events-auto disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-transparent",
        // 底栏的挂断：挂断红的胶囊（白字对比度在 themeContrast.test.ts 里）。
        hangup:
          "rounded-full bg-hangup text-white hover:bg-hangup/90 focus-visible:border-transparent focus-visible:ring-white/60",
        // eidolon: 礼物页底部提示（前景色底的反色胶囊）里的「去对话」：反过来的页面底、前景字（DESIGN.md「礼物页」）。
        toast: "rounded-full bg-background text-foreground hover:bg-background/90",
        // eidolon: 统计页好感度分组收起时的「+N」和展开后的收起键（DESIGN.md「统计页」）：和头像一样的圆，中性底、muted 字。
        more: "rounded-full bg-muted text-subtitle text-muted-foreground tabular-nums hover:bg-muted/70 hover:text-foreground",
      },
      size: {
        default:
          "h-8 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        xs: "h-6 gap-1 rounded-[min(var(--radius-md),10px)] px-2 text-xs in-data-[slot=button-group]:rounded-lg has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3",
        sm: "h-7 gap-1 rounded-[min(var(--radius-md),12px)] px-2.5 text-[0.8rem] in-data-[slot=button-group]:rounded-lg has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 [&_svg:not([class*='size-'])]:size-3.5",
        lg: "h-9 gap-1.5 px-2.5 has-data-[icon=inline-end]:pr-2 has-data-[icon=inline-start]:pl-2",
        icon: "size-8",
        "icon-xs":
          "size-6 rounded-[min(var(--radius-md),10px)] in-data-[slot=button-group]:rounded-lg [&_svg:not([class*='size-'])]:size-3",
        "icon-sm":
          "size-7 rounded-[min(var(--radius-md),12px)] in-data-[slot=button-group]:rounded-lg",
        "icon-lg": "size-9",
        // eidolon: 通话层用的尺寸。call 44px 高，手机上也够触控下限；call-sm 是电脑上头部的「只看文字」。
        call: "h-11 gap-2 px-5",
        "call-sm": "h-9 gap-1.5 px-3",
        // 底栏里的挂断胶囊和 56px 圆键，图标 20px。
        "call-dock": "h-call-dock-control gap-2 px-6 [&_svg:not([class*='size-'])]:size-5",
        "icon-call-dock": "size-call-dock-control [&_svg:not([class*='size-'])]:size-5",
        // eidolon: 通话层输入栏里的 44px 圆键（收起输入栏、发送），图标 20px。
        "icon-call": "size-11 [&_svg:not([class*='size-'])]:size-5",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
