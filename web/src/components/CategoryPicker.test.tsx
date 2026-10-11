import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { renderApp } from "@/test/utils";
import { CategoryPicker } from "./CategoryPicker";

const taxonomy = { groups: [
  { id: "leisure", categories: [{ id: "leisure.outings", description: "Cinema, concerts, museums" }, { id: "leisure.sport", description: "" }] },
  { id: "transport", categories: [{ id: "transport.car_insurance", description: "" }, { id: "transport.fuel", description: "" }] },
  { id: "housing", categories: [{ id: "housing.home_insurance", description: "" }] },
] };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(taxonomy), { status: 200, headers: { "Content-Type": "application/json" } })));
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ onChange, ...p }: { onChange: (v: string) => void; allowEmpty?: boolean; includeGroups?: boolean }) {
  const [v, setV] = useState("");
  return <CategoryPicker {...p} value={v} onChange={(x) => { setV(x); onChange(x); }} />;
}
const names = () => within(screen.getByRole("listbox")).queryAllByRole("option").map((o) => o.textContent);

describe("CategoryPicker", () => {
  it("narrows the list as you type, across groups, ignoring accents and case", async () => {
    const onChange = vi.fn();
    renderApp(<Harness onChange={onChange} />);
    const box = screen.getByRole("combobox");
    await userEvent.click(box);
    await within(screen.getByRole("listbox")).findAllByRole("option");
    await userEvent.type(box, "INSURÄNCE");
    expect(names()).toEqual(["Car insurance", "Home insurance"]);
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenCalledWith("housing.home_insurance");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(box).toHaveValue("Home insurance");
  });

  it("searches the description and the group too, and says when nothing matches", async () => {
    renderApp(<Harness onChange={vi.fn()} />);
    const box = screen.getByRole("combobox");
    await userEvent.click(box);
    await within(screen.getByRole("listbox")).findAllByRole("option");
    await userEvent.type(box, "museum");
    expect(names()).toHaveLength(1);
    await userEvent.clear(box);
    await userEvent.type(box, "transport");
    expect(names()).toHaveLength(2);
    await userEvent.type(box, " zzz");
    expect(screen.getByText("No category matches")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("offers the empty choice and whole groups when asked", async () => {
    const onChange = vi.fn();
    renderApp(<Harness onChange={onChange} allowEmpty includeGroups />);
    await userEvent.click(screen.getByRole("combobox"));
    await within(screen.getByRole("listbox")).findAllByRole("option");
    expect(names()[0]).toBe("Any category");
    await userEvent.click(screen.getByRole("option", { name: /^All transport/i }));
    expect(onChange).toHaveBeenCalledWith("transport");
  });
});
