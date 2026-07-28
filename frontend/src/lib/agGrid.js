import { AllCommunityModule, ModuleRegistry, themeQuartz } from "ag-grid-community";

// AG Grid 36 auto-registers nothing: every feature lives in a module that has
// to be registered once, before the first grid mounts, or the grid renders an
// error placeholder at runtime. The whole community bundle is registered
// rather than a hand-picked list — this is an internal dashboard, so a few
// unused kilobytes cost less than a feature failing the first time someone
// adds a column filter.
ModuleRegistry.registerModules([AllCommunityModule]);

// v36 also replaced the CSS themes (ag-theme-alpine and friends) with a JS
// theme API, so there is no stylesheet to import. Every colour is passed
// through as a `var(...)` reference to the same shadcn token the rest of the
// app uses, which means the grid follows the OS dark-mode switch handled in
// index.css without a second palette to keep in step.
export const agTheme = themeQuartz.withParams({
  accentColor: "var(--primary)",
  backgroundColor: "var(--card)",
  foregroundColor: "var(--foreground)",
  borderColor: "var(--border)",
  browserColorScheme: "inherit",

  // "chrome" is the header row plus the pagination bar — both want the same
  // muted surface the shadcn <TableHead> uses.
  chromeBackgroundColor: "var(--muted)",
  headerTextColor: "var(--foreground)",
  headerFontSize: "12px",
  headerFontWeight: 620,
  headerColumnResizeHandleColor: "var(--input)",

  oddRowBackgroundColor: "transparent",
  rowHoverColor: "var(--muted)",

  fontFamily: "inherit",
  fontSize: "13.5px",
  cellHorizontalPadding: "14px",

  // The Card wrapping the grid already draws the border and rounds the
  // corners; a second one inside it reads as a double rule.
  wrapperBorder: false,
  wrapperBorderRadius: "0",
  borderRadius: "var(--radius-sm)",
});
