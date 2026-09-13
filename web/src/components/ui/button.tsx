import { cn } from "@/lib/utils";
import { ButtonHTMLAttributes } from "react";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "outline" | "ghost";
  size?: "default" | "sm";
};

export function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: Props) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center rounded-md text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:opacity-50",
        variant === "default" &&
          "bg-[var(--ink)] text-[var(--paper)] hover:bg-[var(--ink-soft)]",
        variant === "outline" &&
          "border border-[var(--line)] bg-transparent hover:bg-[var(--panel)]",
        variant === "ghost" && "hover:bg-[var(--panel)]",
        size === "default" && "h-10 px-4 py-2",
        size === "sm" && "h-8 px-3 text-xs",
        className
      )}
      {...props}
    />
  );
}
