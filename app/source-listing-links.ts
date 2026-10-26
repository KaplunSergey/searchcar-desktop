import { SOURCE_CATALOG, sourceDefinition, type SourceCatalog } from "./source-catalog";

export type ListingIdentity = { url: string; source_key: string; source_listing_id: string };
export type ListingValidators = Readonly<Record<string, (url: URL, listingId: string) => boolean>>;

// Detail paths/identifiers belong to each developer-shipped source module.
export const LISTING_VALIDATORS: ListingValidators = {
  copart: (url, id) => url.pathname.match(/^\/(?:[a-z]{2}\/)?lot\/(\d+)(?:\/[^/]+)?\/?$/)?.[1] === id,
  // IAA detail URL uses the vehicle ID, not the stock number stored as listing ID.
  iaa: (url) => /^\/VehicleDetail\/\d+~US\/?$/i.test(url.pathname),
};

export function sourceListingLink(lot: ListingIdentity, catalog: SourceCatalog = SOURCE_CATALOG, validators: ListingValidators = LISTING_VALIDATORS): string | null {
  try {
    const url = new URL(lot.url);
    const source = sourceDefinition(lot.source_key, catalog);
    if (!source || !source.domains.includes(url.hostname) || url.protocol !== "https:" || url.username || url.password || url.port) return null;
    if (Object.hasOwn(validators, lot.source_key) && validators[lot.source_key](url, lot.source_listing_id)) return url.href;
  } catch { /* A missing or malformed saved detail URL stays unavailable. */ }
  return null;
}
