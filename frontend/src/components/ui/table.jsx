"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

// Carries the dashboard's table styling from styles.css: sticky headers over
// a scrolling body, 13.5px rows, and the border/hover pairs from the billing
// palette. Set here rather than at each call site so the four tables in the
// app cannot drift apart. Cells deliberately do not use whitespace-nowrap —
// reason and exception text is expected to wrap.

function Table({
  className,
  ...props
}) {
  return (
    <div data-slot="table-container" className="relative w-full overflow-x-auto">
      <table
        data-slot="table"
        className={cn("w-full caption-bottom border-collapse text-[13.5px]", className)}
        {...props} />
    </div>
  );
}

function TableHeader({
  className,
  ...props
}) {
  return (
    <thead
      data-slot="table-header"
      className={cn(className)}
      {...props} />
  );
}

function TableBody({
  className,
  ...props
}) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0 [&_tr:last-child_td]:border-b-0", className)}
      {...props} />
  );
}

function TableFooter({
  className,
  ...props
}) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t-2 border-app-border-strong bg-app-surface-2 font-[660] [&>tr]:last:border-b-0 [&_td]:border-b-0 [&_td]:px-3.5 [&_td]:py-3",
        className
      )}
      {...props} />
  );
}

function TableRow({
  className,
  ...props
}) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "transition-colors hover:bg-app-surface-2 has-aria-expanded:bg-app-surface-2 data-[state=selected]:bg-app-surface-2",
        className
      )}
      {...props} />
  );
}

function TableHead({
  className,
  ...props
}) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "sticky top-0 z-[2] border-b border-app-border-strong bg-app-surface-2 px-3.5 py-[11px] text-left align-middle text-xs font-[620] tracking-[.02em] whitespace-nowrap text-app-ink-2 [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props} />
  );
}

function TableCell({
  className,
  ...props
}) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "border-b border-app-border px-3.5 py-2.5 align-middle [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props} />
  );
}

function TableCaption({
  className,
  ...props
}) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-sm text-app-muted", className)}
      {...props} />
  );
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
