import assert from "node:assert/strict";
import test from "node:test";
import { filterReportItems } from "../app/report-filter.js";

const items = [
  { id: "new-a", project_id: 1, change: "NEW" },
  { id: "price-b", project_id: 2, change: "PRICE_DROP" },
  { id: "shared", project_id: 1, project_ids: [1, 3], change: "PRICE_DROP" },
  { id: "deleted", project_id: 9, change: "NEW" },
];
const changes = ["NEW", "PRICE_DROP"];
const projects = [1, 2, 3, 9];

test("report filters combine status and project selections without losing history", () => {
  const all = filterReportItems(items, changes, projects, new Set(), new Set());
  assert.deepEqual(all.map((item) => item.id), ["new-a", "price-b", "shared", "deleted"]);

  const onlyPriceInProjectOne = filterReportItems(
    items,
    changes,
    projects,
    new Set(["NEW"]),
    new Set([2, 3, 9]),
  );
  assert.deepEqual(onlyPriceInProjectOne.map((item) => item.id), ["shared"]);

  const deletedProject = filterReportItems(
    items,
    changes,
    projects,
    new Set(["PRICE_DROP"]),
    new Set([1, 2, 3]),
  );
  assert.deepEqual(deletedProject.map((item) => item.id), ["deleted"]);

  const empty = filterReportItems(
    items,
    changes,
    projects,
    new Set(["NEW"]),
    new Set([1, 2, 3]),
  );
  assert.deepEqual(empty, []);

  const resetFallback = filterReportItems(items, changes, projects, new Set(changes), new Set(projects));
  assert.deepEqual(resetFallback.map((item) => item.id), ["new-a", "price-b", "shared", "deleted"]);
});
