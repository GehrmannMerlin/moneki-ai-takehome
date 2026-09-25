import { defineStore } from "pinia";
import type { Filters } from "../api/client";

/** 看板筛选状态：FilterBar 改、TrendChart/TopProducts 消费。 */

export const useFiltersStore = defineStore("filters", {
  state: (): Filters & { highlightWindow: [string, string] | null } => ({
    start: "",
    end: "",
    store_id: "",
    product_id: "",
    /** 图表联动：对话回答里出现的日期窗口，TrendChart 用 markArea 高亮。 */
    highlightWindow: null,
  }),
  actions: {
    setRange(start: string, end: string) {
      this.start = start;
      this.end = end;
    },
    highlight(window: [string, string] | null) {
      this.highlightWindow = window;
    },
  },
});
