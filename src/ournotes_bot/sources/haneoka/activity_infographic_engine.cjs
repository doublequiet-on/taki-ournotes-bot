// Drive the public Haneoka engine with local charts. No network, account API or JS evaluation.
'use strict';

const fs = require('node:fs');

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const binary = fs.readFileSync(process.argv[2]);
  const { instance: { exports: wasm } } = await WebAssembly.instantiate(binary, { env: {
    haneoka_now_ms: () => performance.timeOrigin + performance.now(),
    haneoka_progress: () => {},
  } });

  function call(value) {
    const bytes = Buffer.from(JSON.stringify(value) + '\0');
    const pointer = wasm.haneoka_buffer_alloc(bytes.length);
    new Uint8Array(wasm.memory.buffer, pointer, bytes.length).set(bytes);
    let response;
    try {
      response = wasm.haneoka_context_stream_wasm(pointer);
      const memory = Buffer.from(wasm.memory.buffer);
      const end = memory.indexOf(0, response);
      const result = JSON.parse(memory.toString('utf8', response, end));
      if (result.error) throw new Error(result.error);
      return result;
    } finally {
      if (response) wasm.haneoka_string_free(response);
      wasm.haneoka_buffer_free(pointer, bytes.length);
    }
  }

  const { handle } = call({ op: 'create', data: input.master });
  try {
    const base = {
      members: input.members, snaps: input.snaps, player: input.player, unknownPolicy: 'max', k: 1,
      constraints: { requiredMembers: [], excludedMembers: [], requiredSnaps: [], excludedSnaps: [], bindings: [] },
      timeLimitMs: 40_000,
    };
    const scores = input.charts.map(chart => {
      const goal = { kind: 'score', criterion: 'max', play: {}, challengeEventId: input.eventId, songs: [chart.song] };
      const response = call({ op: 'run', handle, charts: [chart], request: { ...base, goal } });
      if (response.unknownCards?.length) throw new Error('unknown cards in engine input');
      const row = response.results?.[0];
      const hit = response.overall?.[0] ?? row?.hits?.[0];
      if (!hit || hit.score?.orders?.length !== 120) throw new Error('challenge result unavailable');
      return { songId: chart.song.songId, score: hit.score.max, order: hit.score.bestOrder,
        members: hit.members, snaps: hit.snaps, proven: row?.proven === true };
    });
    process.stdout.write(JSON.stringify(scores));
  } finally {
    call({ op: 'dispose', handle });
  }
}

main().catch(error => {
  process.stderr.write(error.message + '\n');
  process.exitCode = 1;
});
