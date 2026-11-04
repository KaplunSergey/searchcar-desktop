# Desktop icon

`app-icon-rounded.png` is the transparent master; `icon.png`, `icon.icns`
and `icon.ico` are generated with the installed Tauri CLI (`tauri icon`).
Only the three generated files are referenced by the bundle configuration.
The original user-provided artwork is retained outside the repository.

The cutout was edited using the built-in image editor, not the API/CLI fallback.
Final edit prompt:

> Background-extraction cleanup only, not redraw. The input has correct transparent corners but an unacceptable ragged cyan halo and isolated pixels outside its tile. Remove ALL of that exterior glow/noise. Alpha must have a single perfectly smooth, antialiased rounded-square/squircle silhouette exactly along the bright cyan border. Beyond the border: alpha zero, no stray pixels, no exterior halo. Keep EVERYTHING INSIDE the border exactly unchanged: blue car, dark magnifying glass, navy tile interior. Do not lighten or make the interior transparent. Uniform transparent safe margin around the icon, centered on square canvas. This is a desktop app icon production cutout, not a creative remix. Clean precise continuous corners. No checkerboard baked in, no text, no added details. Preserve original content.

These are conventional ICNS/ICO assets, not an Icon Composer layered asset.
Appearance on actual Dock/Explorer still needs checking in the next app build.
