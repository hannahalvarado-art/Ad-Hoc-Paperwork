import { mergeProps } from "@base-ui/react/merge-props"
import { useRender } from "@base-ui/react/use-render"
import { cva } from "class-variance-authority";

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "group/badge inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-4xl border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-all focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-3!",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a]:hover:bg-primary/80",
        secondary:
          "bg-secondary text-secondary-foreground [a]:hover:bg-secondary/80",
        destructive:
          "bg-destructive/10 text-destructive focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:focus-visible:ring-destructive/40 [a]:hover:bg-destructive/20",
        outline:
          "border-border text-foreground [a]:hover:bg-muted [a]:hover:text-muted-foreground",
        ghost:
          "hover:bg-muted hover:text-muted-foreground dark:hover:bg-muted/50",
        link: "text-primary underline-offset-4 hover:underline",
        // Billing flags (.p-* in styles.css). The colour is the meaning, so
        // these are not interchangeable with shadcn's neutral variants.
        ok: "bg-ok-soft text-ok",
        conf: "bg-conf-soft text-conf",
        review: "bg-review-soft text-review",
        outlier: "bg-outlier-soft text-outlier",
        missing: "bg-missing-soft text-missing",
        map: "bg-map-soft text-map",
        excluded: "border-app-border-strong bg-app-surface-2 text-app-muted",
        // Period/run status (.status-pill): bordered and uppercase.
        statusNeutral:
          "border-app-border-strong bg-app-surface-2 text-app-muted uppercase tracking-[.03em]",
        statusReview:
          "border-review bg-review-soft text-review uppercase tracking-[.03em]",
        statusOk:
          "border-ok bg-ok-soft text-ok uppercase tracking-[.03em]",
        statusAccent:
          "border-app-accent bg-app-accent-soft text-app-accent uppercase tracking-[.03em]",
        statusMissing:
          "border-missing bg-missing-soft text-missing uppercase tracking-[.03em]",
        statusConf:
          "border-conf bg-conf-soft text-conf uppercase tracking-[.03em]",
      },
      // The .pill::before marker. Kept as its own axis so the status pills,
      // which have no dot, can reuse the same colour variants.
      dot: {
        true: "before:size-1.5 before:shrink-0 before:rounded-full before:bg-current before:opacity-90 before:content-['']",
        false: "",
      },
    },
    defaultVariants: {
      variant: "default",
      dot: false,
    },
  }
)

function Badge({
  className,
  variant = "default",
  dot = false,
  render,
  ...props
}) {
  return useRender({
    defaultTagName: "span",
    props: mergeProps({
      className: cn(badgeVariants({ variant, dot }), className),
    }, props),
    render,
    state: {
      slot: "badge",
      variant,
    },
  });
}

export { Badge, badgeVariants }
