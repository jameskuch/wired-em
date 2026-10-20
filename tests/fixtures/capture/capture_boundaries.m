% Capture the fitted INL/GCL boundary surfaces and the IPL depths derived from
% them. These feed every stratification number the toolbox produces.

root = fileparts(fileparts(fileparts(fileparts(fileparts(mfilename('fullpath'))))));
addpath(genpath(fullfile(root, 'SBFSEM-tools')));
outDir = fullfile(fileparts(mfilename('fullpath')), 'ground_truth');
if ~exist(outDir, 'dir'); mkdir(outDir); end

INL = sbfsem.builtin.INLBoundary('i', true);
GCL = sbfsem.builtin.GCLBoundary('i', true);
fprintf('INL: %u markers, surface %dx%d\n', size(INL.markerLocations,1), size(INL.interpolatedSurface));
fprintf('GCL: %u markers, surface %dx%d\n', size(GCL.markerLocations,1), size(GCL.interpolatedSurface));

dlmwrite(fullfile(outDir, 'inl_surface.csv'), INL.interpolatedSurface, 'precision', '%.17g');
dlmwrite(fullfile(outDir, 'inl_xpts.csv'), INL.newXPts(:), 'precision', '%.17g');
dlmwrite(fullfile(outDir, 'inl_ypts.csv'), INL.newYPts(:), 'precision', '%.17g');
dlmwrite(fullfile(outDir, 'gcl_surface.csv'), GCL.interpolatedSurface, 'precision', '%.17g');
dlmwrite(fullfile(outDir, 'gcl_xpts.csv'), GCL.newXPts(:), 'precision', '%.17g');
dlmwrite(fullfile(outDir, 'gcl_ypts.csv'), GCL.newYPts(:), 'precision', '%.17g');

% A fixed probe grid inside both surfaces, in microns.
[PX, PY] = meshgrid(linspace(20, 240, 12), linspace(20, 240, 12));
probe = [PX(:), PY(:), repmat(60, numel(PX), 1)];
dlmwrite(fullfile(outDir, 'ipl_probe_points.csv'), probe, 'precision', '%.17g');

% What micron2ipl actually returns, bug and all.
iplBuggy = micron2ipl(probe, 'i');
dlmwrite(fullfile(outDir, 'ipl_probe_matlab_asis.csv'), iplBuggy, 'precision', '%.17g');
fprintf('micron2ipl as-is: %u of %u NaN, median %.6f\n', ...
    nnz(isnan(iplBuggy)), numel(iplBuggy), median(iplBuggy, 'omitnan'));

% The same calculation with the INL grid built the way every other copy in the
% repository builds it (stratificationMap.m:36, BoundaryMarker.xyEval,
% getStrataCData.m:38). This is what the Python port computes.
[XG, YG] = meshgrid(GCL.newXPts, GCL.newYPts);
vGCL = interp2(XG, YG, GCL.interpolatedSurface, probe(:,1), probe(:,2));
[XI, YI] = meshgrid(INL.newXPts, INL.newYPts);
vINL = interp2(XI, YI, INL.interpolatedSurface, probe(:,1), probe(:,2));
iplFixed = (probe(:,3) - vINL) ./ (vGCL - vINL);
dlmwrite(fullfile(outDir, 'ipl_probe_matlab_fixed.csv'), iplFixed, 'precision', '%.17g');
fprintf('micron2ipl fixed:  %u of %u NaN, median %.6f\n', ...
    nnz(isnan(iplFixed)), numel(iplFixed), median(iplFixed, 'omitnan'));

d = abs(iplBuggy - iplFixed);
fprintf('BUG IMPACT: max |as-is - fixed| = %.6f over %u finite pairs\n', ...
    max(d(isfinite(d))), nnz(isfinite(d)));

% Depth evaluated at the boundary markers themselves: the ground truth is
% 0 at every INL marker and 1 at every GCL marker.
inlUm = viking2micron(INL.markerLocations, 'i');
gclUm = viking2micron(GCL.markerLocations, 'i');
dlmwrite(fullfile(outDir, 'inl_markers_um.csv'), inlUm, 'precision', '%.17g');
dlmwrite(fullfile(outDir, 'gcl_markers_um.csv'), gclUm, 'precision', '%.17g');

dInl = micron2ipl(inlUm, 'i');
dGcl = micron2ipl(gclUm, 'i');
fprintf('as-is depth at INL markers: median %.4f (want 0)\n', median(dInl, 'omitnan'));
fprintf('as-is depth at GCL markers: median %.4f (want 1)\n', median(dGcl, 'omitnan'));

[XI2, YI2] = meshgrid(INL.newXPts, INL.newYPts);
fInl = (inlUm(:,3) - interp2(XI2, YI2, INL.interpolatedSurface, inlUm(:,1), inlUm(:,2))) ./ ...
       (interp2(XG, YG, GCL.interpolatedSurface, inlUm(:,1), inlUm(:,2)) - ...
        interp2(XI2, YI2, INL.interpolatedSurface, inlUm(:,1), inlUm(:,2)));
fGcl = (gclUm(:,3) - interp2(XI2, YI2, INL.interpolatedSurface, gclUm(:,1), gclUm(:,2))) ./ ...
       (interp2(XG, YG, GCL.interpolatedSurface, gclUm(:,1), gclUm(:,2)) - ...
        interp2(XI2, YI2, INL.interpolatedSurface, gclUm(:,1), gclUm(:,2)));
fprintf('fixed depth at INL markers: median %.4f (want 0)\n', median(fInl, 'omitnan'));
fprintf('fixed depth at GCL markers: median %.4f (want 1)\n', median(fGcl, 'omitnan'));
fprintf('done\n');
