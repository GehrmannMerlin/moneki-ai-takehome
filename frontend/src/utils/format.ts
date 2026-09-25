/** 展示格式化：金额、百分比、涨跌配色（中国惯例：涨红跌绿）。 */

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return "¥" + value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("zh-CN");
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return (value * 100).toFixed(2) + "%";
}

/** 中国市场惯例：涨红、跌绿。 */
export function trendColor(delta: number): string {
  if (delta > 0) return "#d03050";
  if (delta < 0) return "#1a9850";
  return "#888";
}

export function shortDate(iso: string): string {
  return iso.slice(5).replace("-", "/");
}

export const ANSWER_TYPE_LABEL: Record<string, string> = {
  data: "数据",
  doc: "文档",
  hybrid: "混合",
  refusal: "拒答",
  clarify: "反问",
};

export const ANSWER_TYPE_COLOR: Record<string, string> = {
  data: "#2563eb",
  doc: "#7c3aed",
  hybrid: "#0891b2",
  refusal: "#d03050",
  clarify: "#d97706",
};
