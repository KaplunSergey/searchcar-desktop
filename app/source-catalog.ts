import { createContext, useContext } from "react";
import definitions from "../backend/app/sources/catalog.json" with { type: "json" };

export type SourceKey = string;
export type SourceDefinition = {
  key: SourceKey; name: string; family: "MARKETPLACE" | "AUCTION"; ui: string;
  domains: readonly string[]; capabilities: readonly string[];
  image_domains?: readonly string[];
  search_url_example?: string;
  search_selection_help?: { ru: string; uk: string };
};
export type SourceCatalog = readonly SourceDefinition[];
// Trusted build-time metadata, not executable plugins or user-supplied config.
export const SOURCE_CATALOG: SourceCatalog = definitions as SourceCatalog;
export const SourceCatalogContext = createContext<SourceCatalog>(SOURCE_CATALOG);
export const useSourceCatalog = () => useContext(SourceCatalogContext);
export const sourceDefinition = (key: string, catalog = SOURCE_CATALOG) => catalog.find((site) => site.key === key);
export const sourceName = (key: string, catalog = SOURCE_CATALOG) => sourceDefinition(key, catalog)?.name || key;
export const sourceGroups = (catalog = SOURCE_CATALOG) => (["MARKETPLACE", "AUCTION"] as const)
  .map((family) => ({ family, sites: catalog.filter((site) => site.family === family) })).filter((group) => group.sites.length);
