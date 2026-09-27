// @ts-check

/**
 * @typedef {{ project_id: number, project_ids?: number[] }} ReportProject
 */

/**
 * @param {ReportProject} item
 * @returns {number[]}
 */
export function reportProjectIds(item) {
  return item.project_ids?.length ? item.project_ids : [item.project_id];
}

/**
 * @template {ReportProject & { change: string }} T
 * @param {T[]} items
 * @param {string[]} availableChanges
 * @param {number[]} availableProjectIds
 * @param {ReadonlySet<string>} excludedChanges
 * @param {ReadonlySet<number>} excludedProjectIds
 * @returns {T[]}
 */
export function filterReportItems(
  items,
  availableChanges,
  availableProjectIds,
  excludedChanges,
  excludedProjectIds,
) {
  const selectedChanges = availableChanges.filter((change) => !excludedChanges.has(change));
  const selectedProjectIds = availableProjectIds.filter((id) => !excludedProjectIds.has(id));
  const effectiveChanges = new Set(selectedChanges.length ? selectedChanges : availableChanges);
  const effectiveProjectIds = new Set(
    selectedProjectIds.length ? selectedProjectIds : availableProjectIds,
  );
  return items.filter(
    (item) =>
      effectiveChanges.has(item.change)
      && reportProjectIds(item).some((id) => effectiveProjectIds.has(id)),
  );
}
