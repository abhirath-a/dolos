// @vitest-environment jsdom
import { it, expect, vi, afterEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
} from "@testing-library/react";
import { useUpload } from "./useUpload";
import UploadPanel from "./UploadPanel";
import { uploadGroup } from "./api";
vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, uploadGroup: vi.fn() };
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it("cancels remaining uploads without losing completed batches or trapping navigation", async () => {
  const accepted = vi.fn();
  vi.mocked(uploadGroup)
    .mockResolvedValueOnce({
      id: "accepted-batch",
      status: "queued",
      documents: [],
    })
    .mockImplementationOnce(
      (_files, _progress, options) =>
        new Promise((_resolve, reject) =>
          options?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Cancelled", "AbortError")),
          ),
        ),
    );
  function Harness() {
    const upload = useUpload(accepted);
    return (
      <UploadPanel
        upload={upload}
        onOpen={() => {}}
        batches={[]}
        onRetry={() => {}}
        onErrors={() => {}}
      />
    );
  }
  render(<Harness />);
  const files = Array.from(
    { length: 101 },
    (_, i) => new File(["x"], `${i}.pdf`, { type: "application/pdf" }),
  );
  fireEvent.change(screen.getByLabelText("Choose statement files"), {
    target: { files },
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload & build graph" }));
  await waitFor(() =>
    expect(accepted).toHaveBeenCalledWith(expect.any(String), "accepted-batch"),
  );
  await waitFor(() => expect(uploadGroup).toHaveBeenCalledTimes(2));
  fireEvent.click(
    screen.getByRole("button", { name: "Cancel remaining uploads" }),
  );
  await waitFor(() =>
    expect(
      screen.getByText(
        "Remaining uploads cancelled. Accepted batches are preserved.",
      ),
    ).toBeTruthy(),
  );
  expect(
    screen
      .getByRole("button", { name: "Open investigation" })
      .hasAttribute("disabled"),
  ).toBe(false);
  expect(
    screen.getByRole("button", { name: "Resume remaining batches" }),
  ).toBeTruthy();
  expect(accepted).toHaveBeenCalledTimes(1);
});
