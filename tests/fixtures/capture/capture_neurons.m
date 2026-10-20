% Capture per-neuron ground truth: the parsed tables, the segmentation, the
% cylinder mesh and the analyses that run on them.

root = fileparts(fileparts(fileparts(fileparts(fileparts(mfilename('fullpath'))))));
addpath(genpath(fullfile(root, 'SBFSEM-tools')));
outDir = fullfile(fileparts(mfilename('fullpath')), 'ground_truth');
if ~exist(outDir, 'dir'); mkdir(outDir); end

ids = [943, 2542, 7161, 7162];
wantSynapses = [true, true, false, true];

for k = 1:numel(ids)
    id = ids(k);
    pre = sprintf('c%u_', id);
    fprintf('\n===== c%u =====\n', id);
    try
        c = Neuron(id, 'i', wantSynapses(k));
    catch ME
        fprintf('IMPORT FAILED: %s\n', ME.message); continue;
    end

    % --- tables ---------------------------------------------------------
    N = c.nodes;
    cols = [N.ID, N.ParentID, N.VolumeX, N.VolumeY, N.Z, N.Radius, N.X, N.Y, ...
            double(N.OffEdge), double(N.Terminal), N.Geometry, N.XYZum, N.Rum];
    dlmwrite(fullfile(outDir, [pre 'nodes.csv']), cols, 'precision', '%.17g');
    fprintf('nodes %u  (ID ParentID VolumeX VolumeY Z Radius X Y OffEdge Terminal Geometry Xum Yum Zum Rum)\n', height(N));

    if ~isempty(c.edges)
        dlmwrite(fullfile(outDir, [pre 'edges.csv']), [c.edges.ID, c.edges.A, c.edges.B], 'precision', '%.17g');
        fprintf('edges %u\n', height(c.edges));
    end

    if ~isempty(c.synapses)
        S = c.synapses;
        names = arrayfun(@(x) char(x), S.LocalName, 'UniformOutput', false);
        fid = fopen(fullfile(outDir, [pre 'synapses.csv']), 'w');
        for i = 1:height(S)
            fprintf(fid, '%u,%u,%u,%u,%s\n', S.ID(i), S.ParentID(i), S.TypeID(i), S.N(i), names{i});
        end
        fclose(fid);
        fprintf('synapses %u\n', height(S));
    end

    if ~isempty(c.geometries)
        G = c.geometries;
        fid = fopen(fullfile(outDir, [pre 'geometry_summary.csv']), 'w');
        for i = 1:height(G)
            curves = G.Curve{i};
            if ~iscell(curves); curves = {curves}; end
            fprintf(fid, '%u,%u,%u,%.17g,%u', G.ID(i), G.ParentID(i), G.Z(i), G.Zum(i), numel(curves));
            for j = 1:numel(curves)
                fprintf(fid, ',%u', size(curves{j}, 1));
            end
            fprintf(fid, '\n');
        end
        fclose(fid);
        % The first outline's control points, in microns.
        first = G.Curve{1}; if iscell(first); first = first{1}; end
        dlmwrite(fullfile(outDir, [pre 'geometry_first_outline.csv']), first, 'precision', '%.17g');
        fprintf('geometries %u (first outline %u pts)\n', height(G), size(first,1));
    end

    % --- scalars ---------------------------------------------------------
    fid = fopen(fullfile(outDir, [pre 'scalars.csv']), 'w');
    fprintf(fid, 'somaID,%u\n', c.getSomaID());
    fprintf(fid, 'somaRadiusUm,%.17g\n', c.getSomaSize());
    xyz = c.getSomaXYZ(true);   fprintf(fid, 'somaXum,%.17g\nsomaYum,%.17g\nsomaZum,%.17g\n', xyz(1), xyz(2), xyz(3));
    px  = c.getSomaXYZ(false);  fprintf(fid, 'somaXpx,%.17g\nsomaYpx,%.17g\nsomaZpx,%.17g\n', px(1), px(2), px(3));
    fprintf(fid, 'maxRadius,%.17g\n', max(c.nodes.Radius));
    fprintf(fid, 'nCellNodes,%u\n', height(c.getCellNodes()));
    try
        fprintf(fid, 'surfaceArea,%.17g\n', surfaceArea(c));
    catch ME
        fprintf('surfaceArea failed: %s\n', ME.message);
    end
    try
        [ie, ne, ni] = ieRatio(c);
        fprintf(fid, 'ieRatio,%.17g\nnExc,%u\nnInh,%u\n', ie, ne, ni);
    catch ME
        fprintf('ieRatio failed: %s\n', ME.message);
    end
    unf = c.unfinished;
    fprintf(fid, 'nUnfinished,%u\n', numel(unf));
    fclose(fid);
    if ~isempty(unf)
        dlmwrite(fullfile(outDir, [pre 'unfinished.csv']), sort(unf(:)), 'precision', '%.17g');
    end
    fprintf('scalars written\n');

    % --- segmentation ----------------------------------------------------
    try
        [segments, segTable, nodeIDs, startNode] = dendriteSegmentation(c);
        fid = fopen(fullfile(outDir, [pre 'segments.csv']), 'w');
        for i = 1:numel(segments)
            locs = nodeIDs(segments{i});
            fprintf(fid, '%u', locs(1));
            fprintf(fid, ',%u', locs(2:end));
            fprintf(fid, '\n');
        end
        fclose(fid);
        dlmwrite(fullfile(outDir, [pre 'nodeids.csv']), nodeIDs(:), 'precision', '%.17g');
        fid = fopen(fullfile(outDir, [pre 'segmentation_meta.csv']), 'w');
        fprintf(fid, 'nSegments,%u\nstartNode,%u\nstartLocationID,%u\n', ...
            numel(segments), startNode, nodeIDs(startNode));
        fclose(fid);
        fprintf('segments %u, start node %u (location %u)\n', numel(segments), startNode, nodeIDs(startNode));
    catch ME
        fprintf('segmentation failed: %s\n', ME.message);
    end
end
fprintf('\ndone\n');
