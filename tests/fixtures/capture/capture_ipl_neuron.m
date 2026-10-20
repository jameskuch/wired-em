% Full analysis capture on a neuron that sits inside the traced IPL region.
root = fileparts(fileparts(fileparts(fileparts(fileparts(mfilename('fullpath'))))));
addpath(genpath(fullfile(root, 'SBFSEM-tools')));
outDir = fullfile(fileparts(mfilename('fullpath')), 'ground_truth');

id = 1441;   % "S-OFF mGC", an OFF midget ganglion cell
pre = sprintf('c%u_', id);
c = Neuron(id, 'i', true);
fprintf('c%u: %u nodes, %u cell nodes, %u synapses\n', ...
    id, height(c.nodes), height(c.getCellNodes()), height(c.synapses));

N = c.nodes;
dlmwrite(fullfile(outDir, [pre 'nodes.csv']), ...
    [N.ID, N.ParentID, N.VolumeX, N.VolumeY, N.Z, N.Radius, N.X, N.Y, ...
     double(N.OffEdge), double(N.Terminal), N.Geometry, N.XYZum, N.Rum], 'precision', '%.17g');
dlmwrite(fullfile(outDir, [pre 'edges.csv']), [c.edges.ID, c.edges.A, c.edges.B], 'precision', '%.17g');
S = c.synapses;
names = arrayfun(@(x) char(x), S.LocalName, 'UniformOutput', false);
fid = fopen(fullfile(outDir, [pre 'synapses.csv']), 'w');
for i = 1:height(S)
    fprintf(fid, '%u,%u,%u,%u,%s\n', S.ID(i), S.ParentID(i), S.TypeID(i), S.N(i), names{i});
end
fclose(fid);

fid = fopen(fullfile(outDir, [pre 'analyses.csv']), 'w');
fprintf(fid, 'somaID,%u\n', c.getSomaID());
fprintf(fid, 'somaRadiusUm,%.17g\n', c.getSomaSize());
fprintf(fid, 'surfaceArea,%.17g\n', surfaceArea(c));
[ie, ne, ni] = ieRatio(c);
fprintf(fid, 'ieRatio,%.17g\nnExc,%u\nnInh,%u\n', ie, ne, ni);

% --- iplDepth, as shipped (its bin centres are left edges, F123) -----------
[ipl, stats] = iplDepth(c, 'Plot', false);
dlmwrite(fullfile(outDir, [pre 'ipl_percent.csv']), ipl(:), 'precision', '%.17g');
dlmwrite(fullfile(outDir, [pre 'ipl_bins_asis.csv']), stats.bins(:), 'precision', '%.17g');
dlmwrite(fullfile(outDir, [pre 'ipl_counts.csv']), stats.counts(:), 'precision', '%.17g');
fprintf(fid, 'iplN,%u\niplMedian,%.17g\niplMean,%.17g\niplSD,%.17g\niplMode_asis,%.17g\n', ...
    stats.n, stats.median, stats.avg, stats.stdev, stats.mode);
fprintf('iplDepth: n=%u median %.6f mean %.6f mode(as-is) %.6f\n', ...
    stats.n, stats.median, stats.avg, stats.mode);

% The same histogram with true bin centres, which is what the port reports.
[a, b] = histcounts(ipl, 20);
centers = b(1:end-1) + diff(b)/2;
[~, im] = max(a);
fprintf(fid, 'iplMode_centres,%.17g\n', centers(im));
dlmwrite(fullfile(outDir, [pre 'ipl_bin_edges.csv']), b(:), 'precision', '%.17g');
dlmwrite(fullfile(outDir, [pre 'ipl_bins_centres.csv']), centers(:), 'precision', '%.17g');
fprintf('mode: as-is %.6f  true centre %.6f  (bin width %.6f)\n', ...
    stats.mode, centers(im), b(2)-b(1));

% --- dendritic field hull --------------------------------------------------
h = sbfsem.analysis.DendriticFieldHull(c);
fprintf(fid, 'hullArea,%.17g\n', h.data.hullArea);

% --- dendrite diameter -----------------------------------------------------
try
    dd = sbfsem.analysis.DendriteDiameter(c, 'binSize', 5);
    T = dd.data.table;
    writetable(T, fullfile(outDir, [pre 'dendrite_diameter.csv']));
    fprintf('dendrite diameter: %u bins\n', height(T));
catch ME
    fprintf('DendriteDiameter failed: %s\n', ME.message);
end

% --- tortuosity, both branches --------------------------------------------
[G, nodeIDs] = graph(c);
deg1 = find(degree(G) == 1);
a1 = nodeIDs(deg1(1)); b1 = nodeIDs(deg1(2));
fprintf(fid, 'tortA,%u\ntortB,%u\n', a1, b1);
t2 = tortuosity(c, a1, b1, 'Dim', 2);
t3 = tortuosity(c, a1, b1, 'Dim', 3);
bn = c.getBranchNodes(a1, b1); xyz = bn.XYZum;
p2 = sum(euclideanDist2(xyz)); p3 = sum(euclideanDist3(xyz));
c2 = norm(xyz(1,1:2) - xyz(end,1:2)); c3 = norm(xyz(1,:) - xyz(end,:));
fprintf(fid, 'tort2D_asis,%.17g\ntort3D_asis,%.17g\ntort2D_fixed,%.17g\ntort3D_fixed,%.17g\n', ...
    t2, t3, p2/c2, p3/c3);
fprintf(fid, 'branchNodeCount,%u\n', height(bn));
dlmwrite(fullfile(outDir, [pre 'branch_xyz.csv']), xyz, 'precision', '%.17g');

% --- synapse stratification for one type -----------------------------------
try
    xyzSyn = c.getSynapseXYZ('ConvPost');
    iplSyn = micron2ipl(xyzSyn, 'i');
    dlmwrite(fullfile(outDir, [pre 'convpost_xyz.csv']), xyzSyn, 'precision', '%.17g');
    dlmwrite(fullfile(outDir, [pre 'convpost_ipl_asis.csv']), iplSyn(:), 'precision', '%.17g');
    fprintf('ConvPost: %u synapses, median IPL %.6f\n', size(xyzSyn,1), median(iplSyn, 'omitnan'));
catch ME
    fprintf('synapse stratification failed: %s\n', ME.message);
end
fclose(fid);
fprintf('done\n');
