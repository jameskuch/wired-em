% Confirm, by running them, the findings the port documents as fixed.
% Each block reports CONFIRMED (the defect reproduces) or NOT REPRODUCED.

root = fileparts(fileparts(fileparts(fileparts(fileparts(mfilename('fullpath'))))));
addpath(genpath(fullfile(root, 'SBFSEM-tools')));

fprintf('\n== crash-on-first-use findings ==\n');
report('F001 StructureTypes.isBoundaryMarker names a member that does not exist', ...
    @() isBoundaryMarker(sbfsem.core.StructureTypes.Cell));
report('F145 sbfsem.builtin.BloodVessel does not load', ...
    @() sbfsem.builtin.BloodVessel(1, 'i'));
report('F146 sbfsem.builtin.Nucleolus does not load', ...
    @() sbfsem.builtin.Nucleolus('i'));
report('F164 sbfsem.image.ConeOutline does not load', ...
    @() sbfsem.image.ConeOutline(2542));
report('F089 clipMeshByVertices references an undefined cutVerts', ...
    @() clipMeshByVertices(struct('vertices', rand(10,3), 'faces', [1 2 3; 4 5 6]), [1 2]));
report('F142 gauss2 references an undefined x', @() gauss2(1, 1));

c = Neuron(943, 'i', true);

fprintf('\n== NeuronGraph (F002-F006) ==\n');
report('F004 NeuronGraph never assigns obj.neuron', @() localNeuronGraph(c));

fprintf('\n== StructureAPI.graph (F007) ==\n');
report('F007 graph(''Synapses'', true) indexes a column with a table', ...
    @() c.graph('Synapses', true));

fprintf('\n== Neuron.getSynapses called twice (F014) ==\n');
report('F014 second getSynapses concatenates mismatched tables', @() twiceGetSynapses(c));

fprintf('\n== COLLADA (F038) ==\n');
report('F038 COLLADA constructor rejects a real Neuron', @() sbfsem.io.COLLADA(c, tempdir));

fprintf('\n== ieRatio on a synapse-free neuron (F127) ==\n');
report('F127 ieRatio dereferences an empty synapse table', @() ieRatio(Neuron(7161, 'i')));

fprintf('\n== Mitochondria.getVolume formula (F147) ==\n');
r = [0.1; 0.2; 0.3];
matlabWay = 2 * pi * sum(r)^2 * 0.09;
correct   = sum(pi * r.^2) * 0.09;
fprintf('  radii [0.1 0.2 0.3], 0.09 um sections\n');
fprintf('  MATLAB formula 2*pi*(sum r)^2*dz = %.6f um3\n', matlabWay);
fprintf('  correct  sum(pi*r^2)*dz          = %.6f um3   (%.1fx inflated)\n', ...
    correct, matlabWay / correct);

fprintf('\n== micron2ipl grid, the near-duplicates that establish intent (F214) ==\n');
src = fileread(which('micron2ipl'));
hit = regexp(src, 'meshgrid\([^)]*\)', 'match');
for i = 1:numel(hit); fprintf('  micron2ipl.m        %s\n', hit{i}); end
for f = {'stratificationMap', 'getStrataCData'}
    p = which(f{1});
    if ~isempty(p)
        hit = regexp(fileread(p), 'meshgrid\([^)]*\)', 'match');
        for i = 1:numel(hit); fprintf('  %-19s %s\n', [f{1} '.m'], hit{i}); end
    end
end

fprintf('\ndone\n');

function report(tag, fn)
    try
        fn();
        fprintf('  NOT REPRODUCED  %s\n', tag);
    catch ME
        fprintf('  CONFIRMED       %s\n                  -> %s\n', tag, ...
            strtrim(regexprep(ME.message, '\s+', ' ')));
    end
end

function localNeuronGraph(c)
    g = sbfsem.core.NeuronGraph(c);
    g.setIsDirected(true);
end

function twiceGetSynapses(c)
    c.getSynapses();
    c.getSynapses();
end
