import { describe, expect, it } from "vitest";

import { dailyTasksView, withDone, type DailyTasks } from "@/lib/dailyTasks";

function tasks(turns: number, gifts: number): DailyTasks {
  const task = (id: string, progress: number, goal: number) => ({
    id,
    done: progress >= goal,
    progress: Math.min(progress, goal),
    goal,
    draws: 1,
  });
  return {
    tasks: [task("one_turn", turns, 1), task("one_gift", gifts, 1), task("ten_turns", turns, 10)],
    renews_at: "2026-09-25T16:00:00+00:00",
  };
}

describe("每日任务那一节", () => {
  it("一项没做：三行都是空圈，第三项带「0/10」，标题旁「0/3」", () => {
    const view = dailyTasksView(tasks(0, 0), "明天 00:00", false);

    expect(view.count).toBe("0/3");
    expect(view.renews).toBe("明天 00:00 换新");
    expect(view.rows).toEqual([
      { id: "one_turn", name: "和角色有一个回合", isDone: false, progress: null, reward: "抽奖 ×1", action: "converse" },
      { id: "one_gift", name: "送出一件礼物", isDone: false, progress: null, reward: "抽奖 ×1", action: null },
      { id: "ten_turns", name: "和角色们一共有 10 个回合", isDone: false, progress: "0/10", reward: "抽奖 ×1", action: "converse" },
    ]);
  });

  it("说了三句：第一项打勾，第三项「3/10」，标题旁「1/3」", () => {
    const view = dailyTasksView(tasks(3, 0), "明天 00:00", false);

    expect(view.count).toBe("1/3");
    expect(view.rows.map((r) => [r.isDone, r.progress])).toEqual([
      [true, null],
      [false, null],
      [false, "3/10"],
    ]);
  });

  it("第三项做完了不再带进度", () => {
    const view = dailyTasksView(tasks(10, 1), "今天 01:00", false);

    expect(view.count).toBe("3/3");
    expect(view.renews).toBe("今天 01:00 换新");
    expect(view.rows.map((r) => [r.isDone, r.progress])).toEqual([
      [true, null],
      [true, null],
      [true, null],
    ]);
  });

  it("没做完的回合任务带「去对话」；送礼那项背包里有礼物时带「挑一件」，背包空时不带", () => {
    expect(dailyTasksView(tasks(0, 0), "明天 00:00", true).rows.map((r) => r.action)).toEqual([
      "converse",
      "pick",
      "converse",
    ]);
    expect(dailyTasksView(tasks(0, 0), "明天 00:00", false).rows.map((r) => r.action)).toEqual([
      "converse",
      null,
      "converse",
    ]);
  });

  it("做完的那几项不带按钮", () => {
    expect(dailyTasksView(tasks(3, 1), "明天 00:00", true).rows.map((r) => r.action)).toEqual([
      null,
      null,
      "converse",
    ]);
  });

  it("三项都做完：收成一行", () => {
    expect(dailyTasksView(tasks(10, 1), "明天 00:00", true).isAllDone).toBe(true);
    expect(dailyTasksView(tasks(10, 0), "明天 00:00", true).isAllDone).toBe(false);
  });

  it("一项都没有时不算都做完", () => {
    expect(dailyTasksView({ tasks: [], renews_at: "2026-09-25T16:00:00+00:00" }, "明天 00:00", false).isAllDone).toBe(
      false,
    );
  });

  it("后端多给了不认识的一项：照样列出，名字用它的 id", () => {
    const daily = tasks(0, 0);
    daily.tasks.push({ id: "later", done: false, progress: 0, goal: 1, draws: 2 });

    const view = dailyTasksView(daily, "明天 00:00", false);

    expect(view.count).toBe("0/4");
    expect(view.rows[3]).toEqual({ id: "later", name: "later", isDone: false, progress: null, reward: "抽奖 ×2", action: null });
  });
});

describe("送礼到账后当场打勾", () => {
  it("到账里做完的那几项记成做完、进度到目标，别的不动", () => {
    expect(withDone(tasks(9, 0), ["one_gift", "ten_turns"])).toEqual(tasks(10, 1));
  });

  it("不认识的一项不管", () => {
    expect(withDone(tasks(1, 0), ["birthday"])).toEqual(tasks(1, 0));
  });
});
