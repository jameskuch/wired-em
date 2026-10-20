% Capture cylinder-mesh geometry and the analyses that run on a neuron.

root = fileparts(fileparts(fileparts(fileparts(fileparts(mfilename('fullpath'))))));
addpath(genpath(fullfile(root, 'SBFSEM-tools')));
outDir = fullfile(fileparts(mfilename('fullpath')), 'ground_truth');
if ~exist(outDir, 'dir'); mkdir(outDir); end

for id = [943, 7161]
    pre = sprintf('c%u_', id);
    fprintf('\n===== c%u =====\n', id);
    c = Neuron(id, 'i');

    % --- cylinder render -------------------------------------------------
    try
        r = sbfsem.render.Cylinder(c);
        FV = r.allFV;
        dlmwrite(fullfile(outDir, [pre 'mesh_vertices.csv']), FV.vertices, 'precision', '%.17g');
        dlmwrite(fullfile(outDir, [pre 'mesh_faces.csv']), FV.faces, 'precision', '%u');
        fprintf('mesh: %u vertices, %u faces\n', size(FV.vertices,1), size(FV.faces,1));
        bb = [min(FV.vertices); max(FV.vertices)];
        fprintf('bbox x [%.4f %.4f] y [%.4f %.4f] z [%.4f %.4f]\n', ...
            bb(1,1), bb(2,1), bb(1,2), bb(2,2), bb(1,3), bb(2,3));
        % Surface area and enclosed volume of the mesh itself.
        v1 = FV.vertices(FV.faces(:,1),:); v2 = FV.vertices(FV.faces(:,2),:); v3 = FV.vertices(FV.faces(:,3),:);
        area = sum(0.5 * sqrt(sum(cross(v2-v1, v3-v1, 2).^2, 2)));
        vol  = abs(sum(dot(v1, cross(v2, v3, 2), 2)) / 6);
        fprintf('mesh area %.6f um2, enclosed volume %.6f um3\n', area, vol);
        fid = fopen(fullfile(outDir, [pre 'mesh_meta.csv']), 'w');
        fprintf(fid, 'nVertices,%u\nnFaces,%u\narea,%.17g\nvolume,%.17g\n', ...
            size(FV.vertices,1), size(FV.faces,1), area, vol);
        fprintf(fid, 'xmin,%.17g\nxmax,%.17g\nymin,%.17g\nymax,%.17g\nzmin,%.17g\nzmax,%.17g\n', ...
            bb(1,1), bb(2,1), bb(1,2), bb(2,2), bb(1,3), bb(2,3));
        fclose(fid);
    catch ME
        fprintf('cylinder failed: %s\n', ME.message);
        for kk=1:numel(ME.stack), fprintf('  at %s:%d\n', ME.stack(kk).name, ME.stack(kk).line); end
    end

    % --- Line3 on one isolated segment, for a controlled comparison -------
    try
        [~, segTable] = dendriteSegmentation(c);
        xyz = segTable.XYZum{1}; rad = segTable.Rum{1};
        dlmwrite(fullfile(outDir, [pre 'seg1_xyz.csv']), xyz, 'precision', '%.17g');
        dlmwrite(fullfile(outDir, [pre 'seg1_rum.csv']), rad(:), 'precision', '%.17g');
        [gx, gy, gz] = gencyl(xyz', rad);
        dlmwrite(fullfile(outDir, [pre 'seg1_gencyl_x.csv']), gx, 'precision', '%.17g');
        dlmwrite(fullfile(outDir, [pre 'seg1_gencyl_y.csv']), gy, 'precision', '%.17g');
        dlmwrite(fullfile(outDir, [pre 'seg1_gencyl_z.csv']), gz, 'precision', '%.17g');
        fprintf('segment 1: %u annotations, gencyl grid %dx%d\n', size(xyz,1), size(gx,1), size(gx,2));
    catch ME
        fprintf('segment capture failed: %s\n', ME.message);
    end

    % --- analyses --------------------------------------------------------
    fid = fopen(fullfile(outDir, [pre 'analyses.csv']), 'w');
    try
        [ipl, stats] = iplDepth(c, 'Plot', false);
        dlmwrite(fullfile(outDir, [pre 'ipl_percent.csv']), ipl(:), 'precision', '%.17g');
        dlmwrite(fullfile(outDir, [pre 'ipl_bins.csv']), stats.bins(:), 'precision', '%.17g');
        dlmwrite(fullfile(outDir, [pre 'ipl_counts.csv']), stats.counts(:), 'precision', '%.17g');
        fprintf(fid, 'iplMedian,%.17g\niplMean,%.17g\niplN,%u\niplMode,%.17g\n', ...
            stats.median, stats.avg, stats.n, stats.mode);
        fprintf('iplDepth: n=%u median %.6f mode %.6f\n', stats.n, stats.median, stats.mode);
    catch ME
        fprintf('iplDepth failed: %s\n', ME.message);
    end
    try
        h = sbfsem.analysis.DendriticFieldHull(c);
        fprintf(fid, 'hullArea,%.17g\n', h.data.hullArea);
        fprintf('hull area %.6f\n', h.data.hullArea);
    catch ME
        fprintf('hull failed: %s\n', ME.message);
    end
    G = graph(c);
    deg1 = find(degree(G) == 1);
    if numel(deg1) >= 2
        [~, nodeIDs] = graph(c);
        a = nodeIDs(deg1(1)); b = nodeIDs(deg1(2));
        fprintf(fid, 'tortA,%u\ntortB,%u\n', a, b);
        try
            t2 = tortuosity(c, a, b, 'Dim', 2);
            t3 = tortuosity(c, a, b, 'Dim', 3);
            fprintf(fid, 'tort2D_asis,%.17g\ntort3D_asis,%.17g\n', t2, t3);
            % What the two branches would give with the distance functions
            % the other way round, which is what the code intends.
            bn = c.getBranchNodes(a, b); xyz = bn.XYZum;
            p2 = sum(euclideanDist2(xyz)); p3 = sum(euclideanDist3(xyz));
            c2 = norm(xyz(1,1:2) - xyz(end,1:2));
            c3 = norm(xyz(1,:) - xyz(end,:));
            fprintf(fid, 'tort2D_fixed,%.17g\ntort3D_fixed,%.17g\n', p2/c2, p3/c3);
            fprintf('tortuosity as-is 2D %.6f 3D %.6f | fixed 2D %.6f 3D %.6f\n', ...
                t2, t3, p2/c2, p3/c3);
        catch ME
            fprintf('tortuosity failed: %s\n', ME.message);
        end
    end
    fclose(fid);
end
fprintf('\ndone\n');
