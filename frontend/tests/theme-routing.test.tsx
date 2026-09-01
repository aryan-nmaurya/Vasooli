import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ThemeToggle } from "@/components/ThemeToggle";
import { themeScript } from "@/lib/theme";

afterEach(() => {
  window.localStorage.clear();
  window.history.replaceState({}, "", "/");
  document.documentElement.setAttribute("data-theme", "dark");
});

describe("dashboard theme routing", () => {
  it("restores a saved theme on live workspace routes", () => {
    window.history.replaceState({}, "", "/live/recovered");
    window.localStorage.setItem("vasooli-theme", "light");
    expect(window.location.pathname).toBe("/live/recovered");
    expect(themeScript(false)).toContain("var liveRoute");

    window.eval(themeScript(false));

    expect(document.documentElement).toHaveAttribute("data-theme", "light");
  });

  it("keeps public pages dark even when a dashboard preference exists", () => {
    window.history.replaceState({}, "", "/pricing");
    window.localStorage.setItem("vasooli-theme", "light");

    window.eval(themeScript(false));

    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("switches the live dashboard theme interactively", () => {
    render(<ThemeToggle />);

    fireEvent.click(screen.getByRole("button", { name: "Switch to light mode" }));

    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    expect(window.localStorage.getItem("vasooli-theme")).toBe("light");
  });
});
