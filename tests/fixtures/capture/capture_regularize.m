% Capture RegularizeData3D output on fixed inputs, for direct numeric comparison
% against sbfsem.analysis.regularize.regularize_data_3d.
%
% This is the riskiest single port in the whole toolbox: RegularizeData3D fits
% the INL and GCL boundary surfaces, so any numerical difference propagates into
% every IPL depth number produced.

root = fileparts(fileparts(fileparts(fileparts(fileparts(mfilename('fullpath'))))));
addpath(genpath(fullfile(root, 'SBFSEM-tools')));
outDir = fullfile(fileparts(mfilename('fullpath')), 'ground_truth');
if ~exist(outDir, 'dir'); mkdir(outDir); end

% A deterministic scattered dataset. Written out so Python fits the same points.
n = 400;
i = (0:n-1)';
x = mod(i * 7919, 1000) / 100;          % 0..10, deterministic, no RNG
y = mod(i * 6271, 1000) / 100;
z = 0.5*x + 0.2*y + 2*sin(x/3).*cos(y/4);

% writematrix has no precision control; dlmwrite with %.17g preserves the bits.
dlmwrite(fullfile(outDir, 'regularize_input.csv'), [x, y, z], 'precision', '%.17g');

xnodes = linspace(0, 10, 21);
ynodes = linspace(0, 10, 17);
dlmwrite(fullfile(outDir, 'regularize_xnodes.csv'), xnodes(:), 'precision', '%.17g');
dlmwrite(fullfile(outDir, 'regularize_ynodes.csv'), ynodes(:), 'precision', '%.17g');

methods = {'triangle', 'bilinear', 'bicubic', 'nearest'};
smoothness = [0.005, 0.01, 1.0];
for m = 1:numel(methods)
    for s = 1:numel(smoothness)
        try
            zgrid = RegularizeData3D(x, y, z, xnodes, ynodes, ...
                'interp', methods{m}, 'smoothness', smoothness(s));
            fname = sprintf('regularize_%s_%g.csv', methods{m}, smoothness(s));
            dlmwrite(fullfile(outDir, fname), zgrid, 'precision', '%.17g');
            fprintf('OK %s smoothness=%g  size %dx%d  range [%.6f %.6f]\n', ...
                methods{m}, smoothness(s), size(zgrid,1), size(zgrid,2), ...
                min(zgrid(:)), max(zgrid(:)));
        catch ME
            fprintf('FAILED %s %g: %s\n', methods{m}, smoothness(s), ME.message);
        end
    end
end
fprintf('done\n');
