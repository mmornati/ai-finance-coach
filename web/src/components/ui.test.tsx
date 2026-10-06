import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Badge, Button, DiffView, EmptyState, Money, Notice, ProgressBar, Stat } from "./ui";
import { Dialog } from "./ui";

describe("Money", () => {
  it("colours by sign only when asked and keeps tabular numerals", () => {
    const { rerender } = render(<Money v="-12.5" colored />);
    expect(screen.getByText(/12,50/).className).toMatch(/text-neg/);
    expect(screen.getByText(/12,50/).className).toMatch(/num/);
    rerender(<Money v="12.5" colored signed />);
    expect(screen.getByText(/\+12,50/).className).toMatch(/text-pos/);
    rerender(<Money v="12.5" />);
    expect(screen.getByText(/12,50/).className).not.toMatch(/text-pos/);
    rerender(<Money v={null} />);
    expect(screen.getByText("–")).toBeInTheDocument();
  });
});

describe("ProgressBar", () => {
  it("exposes its value to assistive tech and clamps", () => {
    render(<ProgressBar label="Groceries used" value={150} max={100} tone="neg" />);
    const bar = screen.getByRole("progressbar", { name: "Groceries used" });
    expect(bar).toHaveAttribute("aria-valuenow", "100");
  });
});

describe("DiffView", () => {
  it("renders added and removed lines and an empty state", () => {
    const { container, rerender } = render(<DiffView diff={"--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new"} />);
    expect(screen.getByText("+new").className).toMatch(/text-pos/);
    expect(screen.getByText("-old").className).toMatch(/text-neg/);
    expect(container.querySelectorAll("pre")).toHaveLength(1);
    rerender(<DiffView diff="" empty="Nothing changes." />);
    expect(screen.getByText("Nothing changes.")).toBeInTheDocument();
  });
});

describe("Notice, Badge, Stat, EmptyState", () => {
  it("renders readable status without relying on colour", () => {
    render(
      <>
        <Notice tone="neg" title="Broken">Details</Notice>
        <Badge tone="warn">old</Badge>
        <Stat label="Total" value="12" hint="over 3 months" />
        <EmptyState title="Nothing here">Try later</EmptyState>
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Broken");
    expect(screen.getByText("old")).toBeInTheDocument();
    expect(screen.getByText("over 3 months")).toBeInTheDocument();
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
});

describe("Button", () => {
  it("is disabled while busy and fires clicks otherwise", async () => {
    const fn = vi.fn();
    const { rerender } = render(<Button onClick={fn}>Save</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(fn).toHaveBeenCalledTimes(1);
    rerender(<Button onClick={fn} busy>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("Dialog", () => {
  it("opens, labels itself and reports close", async () => {
    const onClose = vi.fn();
    const { rerender } = render(<Dialog open={false} onClose={onClose} title="Sure?">Body</Dialog>);
    expect(screen.queryByText("Body")).not.toBeInTheDocument();
    rerender(<Dialog open onClose={onClose} title="Sure?">Body</Dialog>);
    expect(screen.getByText("Body")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Sure?" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });
});
