import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderApp } from "@/test/utils";
import { CoachText } from "./CoachText";

describe("CoachText markdown", () => {
  it("renders headings, italic, bold, code and lists separated by single newlines", () => {
    const text = "# Weekly summary\n*Every figure is computed by the app.*\n## Spending\nYou spent **12.00 EUR** on `food.work_meals`.\nTop:\n- food.groceries: 5.00 EUR\n- kids.school: 7.00 EUR\n\n1. first\n2. second";
    const { container } = renderApp(<CoachText text={text} />);
    expect(screen.getByRole("heading", { name: "Weekly summary" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Spending" })).toBeInTheDocument();
    expect(container.querySelector("em")).toHaveTextContent("Every figure is computed by the app.");
    expect(container.querySelector("strong")).toHaveTextContent("12.00 EUR");
    expect(container.querySelector("code")).toHaveTextContent("food.work_meals");
    expect(container.querySelectorAll("ul > li")).toHaveLength(2);
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
    expect(container.textContent).not.toMatch(/[#*`]/);
  });

  it("leaves underscores and lone asterisks alone", () => {
    const { container } = renderApp(<CoachText text="food.work_meals and kids.pocket_money, 2 * 3" />);
    expect(container.querySelector("em")).toBeNull();
    expect(container).toHaveTextContent("food.work_meals and kids.pocket_money, 2 * 3");
  });
});
