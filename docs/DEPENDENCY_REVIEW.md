# Dependency and SBOM review

SearchCar does not upload dependency inventories, build caches or vulnerability
reports automatically. This keeps the private repository's GitHub Actions usage
small and avoids publishing internal build information. Generate the inventory
locally before a pilot release, or when changing a dependency.

## Create the SBOM

From the repository root, with Python 3.9 or newer:

```bash
pnpm sbom:generate
```

It writes a CycloneDX 1.6 JSON inventory to
`artifacts/sbom/searchcar-desktop.cdx.json`. The directory is ignored by Git.
The report contains the resolved Node and Rust dependency trees from their lock
files plus the pinned Python build requirements. It has no secrets, customer
data, license state, backups or build output.

The Python requirements file pins direct requirements, not a separate fully
resolved transitive lock. Therefore, keep the exact `pip install` output from a
release build if a regulator or customer later requires a complete Python
transitive inventory.

## Release-time review

Run the commands below manually; none of them changes the repository.

```bash
pnpm install --frozen-lockfile
pnpm audit --prod
python -m pip check
python scripts/generate_sbom.py
```

For a full Python vulnerability check, install `pip-audit` only in a disposable
virtual environment and remove it afterwards. For Rust, use `cargo audit` in a
disposable Cargo tool directory. These tools require their own advisory
databases and are intentionally not part of normal GitHub Actions builds.

Review every newly added direct production dependency for:

- a maintained release and an explicit reason it is needed;
- a license compatible with commercial distribution;
- known critical/high vulnerabilities from the tool's current advisory source;
- whether a native platform or existing dependency can replace it.

Record the SBOM file next to the private release checklist if needed. Do not
upload it to the public releases repository unless a customer explicitly needs
it: that repository should publish only release artifacts, checksums, signatures
and update metadata.

## Scope and limitations

The generated SBOM is an inventory, not a vulnerability assessment and not a
license determination. The report intentionally leaves license fields empty
rather than guessing them from package names. `pnpm audit` depends on the
current npm advisory service; a clean result does not prove the absence of all
vulnerabilities.
