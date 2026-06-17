/**
 * FEAT-001 Phase-1 PASS: LocalDiskStorageAdapter
 *
 * Real assertions against the actual adapter — hermetic, uses os.tmpdir().
 * No test.fails() — every test exercises the live implementation.
 *
 * Covered acceptance criteria:
 *   AC1  upload() writes file under STORAGE_ROOT/<bucket>/<path>
 *   AC2  upload() returns STORAGE_BASE_URL-based publicUrl
 *   AC3  upload() returns matching path / fullPath in response object
 *   AC4  getPublicUrl() composes the correct URL without writing a file
 *   AC5  remove() deletes the file from disk
 *   AC6  exists() returns true when file is present, false when absent
 *   AC8  upload() accepts Buffer body
 *   AC9  upload() accepts string body
 *   AC10 upload() creates intermediate directories (nested path)
 */

import { describe, test, expect, beforeEach } from "vitest";
import { mkdtemp, stat } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { LocalDiskStorageAdapter } from "../local-disk-adapter";
import type { FileStorageAdapter } from "../adapter";

const BASE_URL = "http://localhost:3003/api/storage";

async function makeAdapter(): Promise<{ adapter: FileStorageAdapter; root: string }> {
  const root = await mkdtemp(join(tmpdir(), "feat-1-storage-"));
  const adapter = new LocalDiskStorageAdapter(root, BASE_URL);
  return { adapter, root };
}

describe("LocalDiskStorageAdapter — upload()", () => {
  test("AC1: upload writes file to STORAGE_ROOT/<bucket>/<path>", async () => {
    const { adapter, root } = await makeAdapter();

    await adapter.upload("vault", "user123/avatar.png", Buffer.from("image-data"), "image/png");

    const expectedPath = join(root, "vault", "user123", "avatar.png");
    const info = await stat(expectedPath);
    expect(info.isFile()).toBe(true);
  });

  test("AC2: upload returns a publicUrl composed from STORAGE_BASE_URL", async () => {
    const { adapter } = await makeAdapter();

    const result = await adapter.upload(
      "vault",
      "user123/avatar.png",
      Buffer.from("x"),
      "image/png",
    );

    expect(result.publicUrl).toBe(`${BASE_URL}/vault/user123/avatar.png`);
  });

  test("AC3: upload returns path = filePath and fullPath = bucket/filePath", async () => {
    const { adapter } = await makeAdapter();

    const result = await adapter.upload(
      "imports",
      "user456/tasks.csv",
      Buffer.from("col1,col2\n"),
      "text/csv",
    );

    expect(result.path).toBe("user456/tasks.csv");
    expect(result.fullPath).toBe("imports/user456/tasks.csv");
  });

  test("AC8: upload accepts a raw Buffer body", async () => {
    const { adapter } = await makeAdapter();
    const content = Buffer.from("binary content");

    const result = await adapter.upload("vault", "team1/doc.pdf", content, "application/pdf");

    expect(result.publicUrl).toBe(`${BASE_URL}/vault/team1/doc.pdf`);
  });

  test("AC9: upload accepts a plain string body", async () => {
    const { adapter } = await makeAdapter();

    const result = await adapter.upload("vault", "team1/note.txt", "hello world", "text/plain");

    expect(result.publicUrl).toBe(`${BASE_URL}/vault/team1/note.txt`);
  });

  test("AC10: upload creates nested intermediate directories automatically", async () => {
    const { adapter, root } = await makeAdapter();

    await adapter.upload(
      "vault",
      "user-abc/task-uuid/attachment.pdf",
      Buffer.from("data"),
      "application/pdf",
    );

    const expectedPath = join(root, "vault", "user-abc", "task-uuid", "attachment.pdf");
    const info = await stat(expectedPath);
    expect(info.isFile()).toBe(true);
  });
});

describe("LocalDiskStorageAdapter — getPublicUrl()", () => {
  test("AC4a: getPublicUrl returns correct URL for vault bucket", () => {
    const adapter = new LocalDiskStorageAdapter("/tmp/any-root", BASE_URL);

    const url = adapter.getPublicUrl("vault", "user123/avatar.png");

    expect(url).toBe(`${BASE_URL}/vault/user123/avatar.png`);
  });

  test("AC4b: getPublicUrl works for imports bucket", () => {
    const adapter = new LocalDiskStorageAdapter("/tmp/any-root", BASE_URL);

    const url = adapter.getPublicUrl("imports", "user456/tasks.csv");

    expect(url).toBe(`${BASE_URL}/imports/user456/tasks.csv`);
  });
});

describe("LocalDiskStorageAdapter — remove()", () => {
  test("AC5a: remove() deletes an existing file from disk", async () => {
    const { adapter } = await makeAdapter();

    await adapter.upload("vault", "user123/avatar.png", Buffer.from("image"), "image/png");
    const presentBefore = await adapter.exists("vault", "user123/avatar.png");
    expect(presentBefore).toBe(true);

    await adapter.remove("vault", "user123/avatar.png");

    const presentAfter = await adapter.exists("vault", "user123/avatar.png");
    expect(presentAfter).toBe(false);
  });

  test("AC5b: remove() on a non-existent path does not throw", async () => {
    const { adapter } = await makeAdapter();

    let threw = false;
    try {
      await adapter.remove("vault", "ghost/file.png");
    } catch {
      threw = true;
    }
    expect(threw).toBe(false);
  });
});

describe("LocalDiskStorageAdapter — exists()", () => {
  test("AC6a: exists() returns false when file is absent", async () => {
    const { adapter } = await makeAdapter();

    const result = await adapter.exists("vault", "nobody/nothing.png");

    expect(result).toBe(false);
  });

  test("AC6b: exists() returns true after upload", async () => {
    const { adapter } = await makeAdapter();

    await adapter.upload("vault", "team1/file.txt", "content", "text/plain");

    const result = await adapter.exists("vault", "team1/file.txt");

    expect(result).toBe(true);
  });
});

describe("FileStorageAdapter — interface type export", () => {
  test("adapter module exports FileStorageAdapter interface (type check via import)", async () => {
    const mod = await import("../adapter");
    expect(typeof mod).toBe("object");
  });
});

describe("LocalDiskStorageAdapter — path traversal rejection", () => {
  test("upload() throws when bucket contains '..'", async () => {
    const { adapter } = await makeAdapter();
    await expect(adapter.upload("..", "file.txt", "x")).rejects.toThrow("Path escapes storage root");
  });

  test("exists() throws when filePath contains '../' traversal", async () => {
    const { adapter } = await makeAdapter();
    await expect(adapter.exists("vault", "../../../etc/passwd")).rejects.toThrow("Path escapes storage root");
  });

  test("download() throws when combined path escapes root", async () => {
    const { adapter } = await makeAdapter();
    await expect(adapter.download("vault", "../../secret")).rejects.toThrow("Path escapes storage root");
  });

  test("remove() throws when bucket is '..'", async () => {
    const { adapter } = await makeAdapter();
    await expect(adapter.remove("..", "file.txt")).rejects.toThrow("Path escapes storage root");
  });
});
