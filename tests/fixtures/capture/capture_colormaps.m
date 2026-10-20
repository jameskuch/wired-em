% Capture every colormap sbfsem.ui.ColorMaps offers, as the RenderApp's Colormap
% menu delivered them, for direct numeric comparison against
% sbfsem.util.colormaps.
%
% The vendored map files (crameri.m, pmkmp.m, othercolor.m, lbmap.m, haxby.m,
% viridis.m, antijet.m, isolum.m, ametrine.m) are pure data, so the port ships
% these tables rather than re-vendoring the files. Two lengths are recorded:
% 256, the menu default, and 16, to pin how getColormap resamples when the
% Levels box is edited.
%
% Rows are stacked in enumeration order (Spectral, Bone, CubicL, RdYlGn, Gray,
% Haxby, Hsv, Roma, Batlow, Hawaii, Devon, AntiJet, Parula, RedBlue, Viridis,
% StepSeq, Ametrine, Isolum), N rows per map, columns R G B in 0-1.
%
% ColorMaps.RedBlue applies fliplr to the Nx3 lbmap table, which swaps the R
% and B channels rather than reversing the map; the raw lbmap table is written
% too so the port's documented correction (flipud) can be asserted against it.

root = fileparts(fileparts(fileparts(fileparts(fileparts(mfilename('fullpath'))))));
addpath(genpath(fullfile(root, 'SBFSEM-tools')));
outDir = fullfile(fileparts(mfilename('fullpath')), 'ground_truth');
if ~exist(outDir, 'dir'); mkdir(outDir); end

import sbfsem.ui.ColorMaps
names = {'Spectral', 'Bone', 'CubicL', 'RdYlGn', 'Gray', 'Haxby', 'Hsv', ...
    'Roma', 'Batlow', 'Hawaii', 'Devon', 'AntiJet', 'Parula', 'RedBlue', ...
    'Viridis', 'StepSeq', 'Ametrine', 'Isolum'};

for N = [256, 16]
    rows = zeros(0, 3);
    for i = 1:numel(names)
        cmap = ColorMaps.(names{i}).getColormap(N);
        assert(isequal(size(cmap), [N, 3]), '%s returned %dx%d at N=%d', ...
            names{i}, size(cmap, 1), size(cmap, 2), N);
        rows = [rows; cmap]; %#ok<AGROW>
    end
    dlmwrite(fullfile(outDir, sprintf('colormaps_%d.csv', N)), rows, 'precision', '%.17g');
end

dlmwrite(fullfile(outDir, 'colormap_redblue_lbmap_raw.csv'), lbmap(256, 'RedBlue'), ...
    'precision', '%.17g');
disp('capture_colormaps: done');
