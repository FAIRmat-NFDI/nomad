## 2.0.1 (2026-11-06)

### Fixed (5 changes)

- [Fixed graph queries for legacy archives using known entry IDs](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/caa3822f3b3471ac8816cc064815ce08b77b4bc3) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3283))
- [Fixed issue in writing HDF5 archive files for published projects](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b38976ae11d286e1e30533aff1184f87f563ac6a) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3286))
- [Fixed issue in Green's function missing from simulation entry layouts](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/86efbaf92ad92a8459434944f7a154b8c2c9dfed) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3278))
- [Fixed issue with reading list values in the legacy archive browser](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/26cabb5b25c36ef97eb804eb8f5680b52ac5a4aa) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3292))
- [Fixed issue with project DOIs leading to the wrong address](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/80fde4c2d61e8311c4583340079cc9205a05fb79) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3289))

## 2.0.0 (2026-10-18)

### Added (8 changes)

- [Added cabability for a project to include entries from other projects.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/fad4f15f96d66c3a033ec1ebb7afda99f9d84c38) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3253))
- [Added graph API pagination features and added option to disable quantity fetching.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/48c2657740c432906ebf05215cbf6f69c7c105ba) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3254))
- [Added support for Elasticsearch 9.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b6ad36f36e547029f425bf571df4f3b5bf1c4bed) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2907))
- [Added user notifications endpoint.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e751339723af42f32596e2d872a100dc14288bf8) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3136))
- [Added process status field into archive metadata.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c32dbbf94c3ee4c008689f99ecda1dc7b07bbe9f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3116))
- [Added group ownership transfer feature](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1ad3714919d5785517af54aba4c185f2ce5150de) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3049))
- [Added new configuration field for setting documentation URL.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6bc7ff69d6a4e8227c393b58b15717fe4ff272a7) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3258))
- [Added new configuration fields for dashboards.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1346d8de0385e38d50bc89d45c21af15f4e9bdbc) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3269))

### Changed (2 changes)

- [Changed default mounting path for APIs and dashboards.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/baca1eb12dd7aeded00e9a368a40a678bd7c9db6) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3238))
- [Optimized archive reading RAM usage by using streaming.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/0d80f62fec8340aded35087492091ee28ad87e94) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3181))

### Removed (5 changes)

- [Removed use of deprecated menu items from default apps that ship with NOMAD.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c3ca9005e588f6fab78ff223996985c8c1f88878) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3281))
- [Removed deprecated PlotAnnotation class.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/41158c6b1d1e7e4008aa430e474d9074de80f604) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3248))
- [Removed many of the deprecated configuration options.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f765dfce8ef52f4940119ad270d5aa7a01967bfe) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3249))
- [Removed log transfer feature.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/fac5dac87847b719d658ed82dd0ec41256f5cec5) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3219))
- [Removed the materials indexing feature](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6406203fad26743f8cb914092e155b2f7ca56849) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3221))

### Fixed (6 changes)

- [Fixed issue with complex queries not passing app validation.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/7b6e153591658d9523bf4a336a2933662a4bc3a7) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3264))
- [Fixed issue with filepaths not accepting whitespace characters.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a2557a4c4266d88a4ad7efec9748f243ae0eb45f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3211))
- [Fixed issue with archives not always being generated for mainfiles.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/7d74ac1a280c916481d7efb538592c4a626b0f5d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3226))
- [Skip results normalizer if workflow comes from nomad-simulations](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/30b07a6993ebdd7fcc118873466c89dbac6f0c88) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3171))
- [Fixed issue with NORTH launch row action not working.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/89677e1c53f22261ae505a04717a85369b6fb679) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3123))
- [Fixed issue with resource downloading blocking the event loop during API calls.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/ab6b9f5516d373c313c1a1e62431f9294166bb69) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3118))

## 1.4.3 (2026-07-13)

### Changed (4 changes)

- [Preload parsers during worker startup.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/0c5f46f4446e2406391533e9cd2cdba8a852348d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3007))
- [Changed the archive reader/writer to use the new v3 format that is more memory-efficient.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/ae38d03c1f53680294a7067a0b03f66faeb4ca25) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2862))
- [Better error messages when processing does not finish within API timeout limit](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d05807e65fccbb5e8adf67c454c5445fc95af60a) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3079))
- [Fixing memory leak in archive reading and made archive reading endpoints synchronous.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/28ab3b9ae6a0e452efe9a06c1e42a0116b6cd442) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3076))

### Added (12 changes)

- [Added new plugin entry point type for Dashboards](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/112e377334e346b92d7388420750deda09903e6f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3001))
- [Added upload ownership transfer API](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/64c5dcde8094a4ea8def0949324ee3c7c882185f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2971))
- [Added opentelemetry and tracing](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/da568c00298bc65d629174d76a3e7b568f99ff0a) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2931))
- [Reuse runtime in temporal client and allow setting api_key and tls config](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/8447520903539e9896879d65b1ff6ab9515bd0ff) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3074))
- [Add health endpoint to workers.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6ed306d06b60e78d1b213614a4117ba18615dd31) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3073))
- [Add action asset upload and consumption feature.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/5a864db6c0ae9e4e1a4569d1624a2a776e017888) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2934))
- [Support Temporal Workflow Streams.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f19b55cdcf6e82bdfc9b6596128d86b35c1c4dd3) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3044))
- [Add wait for processing in upload endpoints for blocking workflow tasks.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/72363a7c4c5381ed360ead1d6f52b9168d32ae26) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3053))
- [Add priority key and priority fairness key for actions entry point.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c4cd78296f14c137d1e1b8d07afca778c27168a2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3057))
- [Implement support for multiple annotations.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4ec8a19330e3cd2a474aa0dce38fb0c6060087ac) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3040))
- [Allow limiting action entry point access to specific userids/groups.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c4b1cae0557b36cedb06d2bfe66a6a79fe2a5bbe) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3027))
- [Added refactored NORTH API and a configuration flag to enable it.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c9ecbac2859ed8aa81c58da8e857e4386940cd5f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2881))

### Fixed (6 changes)

- [Fixed issue with HDF5 files not being accessible without login.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/dd84b5bafe311aed14cbbfefd6351b3014b05c69) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/3081))
- [Fixed issue with API entrypoint URLs not working without slash.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c60bc23c6c8e200ba617a279e9e57b6fb615da10) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2942))
- [Fixed issue in resolving schemas using the API.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d4117d4161db253ccafef6d45d6ee51b1605d1c9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2992))
- [Fixed issue with HDF5 file preview not working due to URL redirects.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b6f5badb557c3ddf3ddc0deed0d04739e2f63f8c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2991))
- [Fixed issue with the JSONSchema export of self-referencing schemas.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/2b41084438a27e471560d34a4f3fde53e31f6267) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2988))

## 1.4.2 (2026-04-17)

### Fixed (6 changes)

- [Fixed issue in parsing non-scalar config values from environment variables.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d76801bcd5dc95afd4197b4ae3b344898fb6a84d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2994))
- [Fixed issue in resolving schemas with version tags.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6e12284f6ec67bfce7e4b5d714c3dd2a50e7c7a2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2992))
- [Fixed issue with HDF5 file preview not working due to URL redirects.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/54e9c127418a8b80661b5b44e64e35500b9aaf1e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2991))
- [Fixed issue with the JSONSchema export of self-referencing schemas.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f09a508270f76f59cb66be28bd577ea95f83b2a4) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2988))
- [Fixed `upload ls --unindexed` command.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4f449d5452ce95eb5d658bba271239a2621dd954) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2967))
- [Fixed issue with not being able to use units with constants in the UI.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1e41b358423d7a0ef2d79198269b0b186affd39e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2963))

### Removed (2 changes)

- [Removed NORTH tools from defaults.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4fb74cbe3739dd397cce5e67b59d44d4ab38e8f5) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2964))
- [Removed the `materials` API endpoint.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/28eac7e96633b3305f4b36320e24a14d8c356e92) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2911))

### Added (14 changes)

- [Added new unit for electric field gradient.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/3672d5cd3f81ba178f056698713bf223dd3e1afe) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2965))
- [Added query capabilities for personal access tokens.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/14a72eea9176cffd4b9357a758bdf66652b01f23) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2916))
- [Allow actions to request user input for human-in-the-loop interactions.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/28a45cfef4cb5ac1f85feb4dc970719b3ff78626) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2893))
- [Added possibility to create DOIs for uploads through the API.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/bad82b09c01043502d5111fe00f1603541ad0ff2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2886))
- [Added new widget type for box plots, added initial support for percentiles aggregation.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/de919a9b032c06c18125100be6b1d128f08966dd) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2951))
- [Add pagination to get actions endpoint](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/69ca2b28a21b3eb97932921fce8d9c8f5e3bd640) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2948))
- [Resolve "Compressed POTCAR files not stripped"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e9fcb879afbbecb07cdf61469b777eff473fecad) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2915))
- [Added a new `fs.actions` file location for storing action logs and artifacts.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/8b2c90b5a0f51d75bb35c13084bc42595b6d7cb2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2892))
- [Added priority levels to data processing workflows.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/324ac587bcef4f2031d3dbefc858dae1b4cd2a34) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2913))
- [Added implementation for personal access tokens.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f008cc90c41c8f49b4b60027898917f457396541) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2875))
- [Added new `schemas` API endpoint that also supports JSONSchema format.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1904401038b926b9b26f9e97cd191adf1edad4f5) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2499))
- [Add temporal worker tuner.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/23a67f3597153555c604b2c1d411f33fe103c339) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2874))
- [Added option to API endpoints to avoid triggering processing of data upon upload content change.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c2a433217a9254d1510b926beacff7d3ada95f2a) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2861))
- [Added improved authorization/authentication options in the NOMAD configuration.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a7c7aaeb81a799d56780e5d2a658d36dd76a9d4e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2854))

### Changed (7 changes)

- [Optimized the file matching and cleanup activities during data processing.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b2eec052a9c599ae3f78a0f481e2e3937f0ea3cd) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2953))
- [Action documents migrate to async mongo + beanie](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/fad62fcd3bf98f70402e4b595cca54081bc0352b) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2905))
- [Improved processing throughput for uploads with large amounts of small entries.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/7874efdf1998db4430e6170a0fd752d3116cc600) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2920))
- [Updated h5grove to version >=3.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4ac348c835f85b6f93116c016042e80ed22a72cd) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2895))
- [Changed the temporal entry batch processing logic to better utilize CPU.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/2c0c77702e2e748a86b8840fa90d973cbd2b24e7) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2872))
- [Increased the processing throughput by simplifying the workflow structure.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1883bf67cec2a114a6762e9bde26d41428909a7c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2867))
- [Changed the interface for reading file to use context managers.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f6c0dcb5cdd8023d01ff51018b68cf0e442ee228) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2856))

## 1.4.1 (2026-02-06)

### Added (13 changes)

- [Added possibility to defined NORTH tools as plugin entry points](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/2f36175d21b0f33f07edb86854df186bd09cf7ed) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2655))
- [Added temporal heartbeats and timeouts to internal processing activities.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e1ef72272cbe3483d2141e99bb7f88127560f7f0) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2832))
- [Added description field for uploads.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/ee8688af3dea2f5b19fc50525295a9d3fd8efb47) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2840))
- [Added new plugin entry point attribute for setting up an identifier that can...](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/8f35654c73eff8f7c0bd314ac1943747b3090bcf) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2835))
- [Add prometheus runtime to Temporal client](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/89a391934ad9880fcbccc9238c70db4c71b989bf) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2834))
- [Added line-by-line parsing option to text parsing.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/16fa52c1f95fa40ed53a15eb5ba9974daa40c3e4) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2802))
- [Added possibility to control default upload and upload entry pagination from nomad.yaml.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/7b2400707db9258daa3dc5962c1dff20d77ce872) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2810))
- [Added max-tasks-per-child argument to control the rebooting of the worker process.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/5f6648912153c5d996da08383e7a006ff486e125) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2769))
- [Added Message-ID to emails that are sent after processing is completed.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/bf4ad5efef886296a9b6269a037654b015ae98c3) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2798))
- [Added new CLI command for unpublishing uploads.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/10638105851d74a94d58fdebf0183d570834afdd) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2773))
- [Added description field for widgets.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/ded889a2186ad15776c4fff0605cb0664242c231) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2640))
- [Added possibility to define group member roles.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/499716b5acfc4c7acfe6826c67811a7131046d7d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2657))
- [Added check for metainfo quantity/subsection name validity.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/666b2a7af97ff87d44bb542f7926c1f600c10b4f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2685))

### Fixed (11 changes)

- [Fixed issues with building histogram aggregations for negative floating point numbers.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/16a54ecce85bcaa7a90e94e223e4cb3fb84a9dfb) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2846))
- [Fixed issue with action entry point loading not taking the full configuration into account.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/524a703fccf347d27340064ee0b9a4abad7254f4) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2845))
- [Fixed issue with processing level check when not in parsing context.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/08da4c8a3332e6bc8507ec714a04445cd1cdedde) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2829))
- [Fixed issue with file copy/move operation response code.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/77211d5d2191a888f0dbb214b16b2777fa88e13d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2809))
- [Fixed issue with scatter plot region selection for search quantities with data types.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/aedbd9cb483ef22c6a4719eca5c39936f9d02932) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2786))
- [Fixed issue with downloading archive files.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d7e4a5bfbb30c0c39d1ee426d14cb2bbee7cea4e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2775))
- [Fixed issue with viewing folders that have numeric names](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/60ca99f547d6b4cd4c7f3579a5068465b686becd) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2768))
- [Fix references check on delete entry action](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/05d481359229c026ce851720d54298385637f09c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2770))
- [Enable resolving even with processing errors](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/86ef78de402203619e673c8c49bc0a16f9d0ee93) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2579))
- [Fix links to docs API page](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a0e3f56716374780ffc328b56be4b8f600a2f624) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2749))
- [Fixed issue with display annotations.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/3ef6d267d5d40cac2008bf7199e9a290d41a7be5) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2718))

### Changed (8 changes)

- [Moved the possibly large GUI static file from RAM to the fs.tmp folder.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/8826cb544748c7aefe562397f1df13d201e7261e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2826))
- [Declare starlette min to ensure availability of HTTP_422_UNPROCESSABLE_CONTENT](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a24e3ab773713e513ca0cac3661d862183e7705f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2828))
- [Remove dependency of results datamodel on simulationworkflowschema.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/0f9d5b978e3b4897533d8d7154eb6ee14fcf7cd2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2767))
- [Changed the processing to use dynamic search document bulk ingest batching based on document sizes.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/612690004773f289621f41f6a8df83f7b95ff99e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2771))
- [Set individual timeouts for upload processing activities](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/ed75818e35f7537331df05fb8e425d9b36b04bfc) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2781))
- [Improve text parser](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d360595bfbfe28a8aca7489fd03a46ec820515f6) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2739))
- [Pass upload token via header](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e58f29bab5f7e19004d97bacd2a9c2821f035832) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2708))
- [Resolve "Basesection v2 is missing normalize function"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/5213475a123f56f7a3e210b0def8fed592b263b2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2704))

### Removed (1 change)

- [Removed the deprecated plugin models and their loading from YAML files.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/8c1570c0060bc64459caf9cc9b251acf476c40ea) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2800))

## 1.4.0 (2025-10-31)

### Changed (9 changes)

- [Relax numpy upper bound constraint, bump `pymatgen` (which would also unblock Python 3.13 support)](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a977ad3a874a4d6ad97f08b29d44a6ed76a76dda) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2694))
- [Disabled materials indexing by default](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/cde085ee8e2bb059a0fec7fa1329019f0605368b) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2688))
- [Changed processing to use temporal by default](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/bec30f63a8c6767fb0084eeb8f19a6266cc9d095) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2656))
- [Enable writing definition IDs to archive and storing custom definitions in MongoDB](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6a308c7286886af43e41a621456ae28503926b68) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2590))
- [Changed default value of `config.services.api_base_path` from...](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/70585cff9d098a8c41126d3c83581c9d62df1387) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2598))
- [Pass context to sub-section](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f7971f7d09aa374f0c4555413cdf582f7508c271) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2577))
- [Changed visualization of matrices from column-major to row-major ordering](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b902741ada92234df3bf47783f369a8dc30c9548) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2558))
- [Bump ase to 3.25.0](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/aefdb4e38ffc1f088ff272aa99a227c7c22c4519) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2563))
- [Refactored upload transfer UI](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e403dcd079033965ea32e32fe20d294b9c2c879e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2635))

### Added (13 changes)

- [Added support for dimensionless units such as percent, permille and ppm](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/85309221bf2de523ad0fe933790e155e1fec36ad) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2687))
- [Added consistent templating for Plotly plots](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/bfa8442b646b5a0cd1f4c4f95faea665a0a0e8ce) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2586))
- [Added support for providing the filepath of new entries created from the GUI](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/df1d7a7e831e0fa8e5b120d7fecf5edc6af5b845) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2638))
- [Added API endpoints for interacting with NOMAD Actions](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/493bcf693275c952750158f6fdcfc07775c789d7) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2624))
- [Added option to specify multiple configuration files for the `nomad run` command](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a09e00e2384fbfaa08895c16754f9fad80833dd4) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2542))
- [Added new plugin entry point type for actions](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/3653a2bb28c5ab9bb181f5b6b8c6061a9ee852e5) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2496))
- [Add button to stop upload processing when temporal is enabled](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e111382c04227644960d8fbc8fce0779d72c3c18) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2559))
- [Added ElnMatchingParser](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/2578a7f7464c94a945b4b01936a134f880d11783) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2453))
- [Added option for requiring authentication to access sensitive information through API](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6c2af564d4cf2a6e72d7262dfe8f2201ee854039) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2509))
- [Added possibility to request GPU devices in the NORTH tool configuration](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/59e06c58d8c1aefd22819f18c5965630d2101bb6) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2471))
- [Added authentication source to 401 HTTP response headers](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/575c465d3711c2424443635c078a2ad5168fbad4) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2648))
- [Added new upload transfer endpoint](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c9b0a4e1a5fc3e66f8a317fbbff1edfbb63f3fb1) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2552))
- [Added setting for controlling production/development mode](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/cf0fbbe5672863db7904da63fe79157c80d65e42) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2700))

### Fixed (9 changes)

- [Fix unnecessary nesting of sub-sections](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/690a902782a5355f0890cb2695abaae61ed50df8) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2557))
- [Fixed issue in parsing JMESPath operators with filter expressions containing multiple conditions](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/28517c70809f17dc58ce991bef71c8db449eb33d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2675))
- [Fixed issue with slow Apps API response](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/67dbd6ad219d40242086b803828b02decdf5ad73) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2651))
- [Fixed issue with null references in graph API](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/9e996bed6c34d520f5cefbb4c6d445d6326c63e8) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2647))
- [Fixed user dependency issue with h5groove app](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/600540509cbf4aeb732d538572bb3f7bed60e889) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2617))
- [Fixed issue with long error message formatting when viewing uploads in the GUI](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/fb3230188641ae80ec77b89a0a229bca326b0846) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2576))
- [Fixed issue in downloading data for multiple selected entries](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1b3f4bac0a848316e3e0a2ecc522d2bd8ad0a79c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2533))
- [Fix FHI-aims method normalizer](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/68c7ab9fbcf54bfaae490b4c792238c992c1559c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2536))
- [Fix issues with POTCAR stripping and removal](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/385b254afe2e7ac10a5c2c246e3511232ae37c1c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2550))

### Removed (4 changes)

- [Remove resources app](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a330922423c5f2a6e5e49ba83e8e6adea84ccffa) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2564))
- [Removed the materials app](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/abd8bdee3e398737dce52a4d8442b745050743c6) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2658))
- [Removed GET endpoint for fetching bearer token](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/67d8c35a1d55ef573f8ec4a504e1e63c5fb55272) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2665))
- [Remove unused basic_auth](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/caecfb33ce51747c49d4dedda6a81e77d0e9c697) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2678))

## 1.3.17 (2025-08-25)

### Added (11 changes)

- [Add option to use temporal for data processing. Disabled by default via feature flag.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d3f8b4d55eec4f0ce0c3ef61d4e06a1a7b65bc43) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2511))
- [Added app API endpoints](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d222354f4a56d2b65d78419d21bff67a87ce6cdb) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2516))
- [Added option to display custom footer links on home page](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/eec52013d8240801e3d8ae72e9e156d7a4334a78) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2514))
- [Added parameter that controls automatic data decompression when sending files with the API.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/5a1be8ea53ebf237fc4accda058eb0aff1502e57) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2480))
- [Added possibility to request GPU devices in the NORTH tool configuration.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c1b31035088e043c454fb01fe8c454d7d71e3f15)
- [Added button for copying definition identifiers in the metainfo browser](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/db67c67dbc53dcc78d9a8f66edd7307164f9e71a) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2494))
- [Added option to use custom terms of service link](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/93e98d33d8f0518034b55d17486352c84b9a89fb) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2488))
- [Added support for multiple dimensionless units in the GUI unit system](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/7865dfeb2ee7c060da80f118f46f9e0cac40f517) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2500))
- [Added unit support for count](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/774ddf8120b8908a00b440058b0fe05915748ca9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2500))
- [Added support for using JMESPath map function with expression reference](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d67188fd2eecf3c3a1c3baadfe384e399895957d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2463))
- [Extended reference format in the GUI](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d60d88bf00ce29fb5b315149a6fa1157366f3923) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2462))

### Changed (4 changes)

- [Updated Pint dependency to >=0.23](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/06754b3f98ff71c748fb1ec3f1a98d2482418686) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2539))
- [Moved documentation to a separate repository](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/afa423beb97cead96daf18431099fd7324d258e2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2444))
- [Changed SampleHistoryCard query to use v1 base sections](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e835c7fbbbf7264825e69cb1f90fc343b5315c42) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2477))
- [Results normalizers assigns entry_type only when it has not been defined](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4d219bc561c5cdead72d7437c6983bd16158d61f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2497))

### Fixed (6 changes)

- [Fix PubChem API calls failing due to change to their API](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4315953e275cadf7811e3cf294a6a8e390ff53d2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2515))
- [Fixed issue with parsers meant for testing purposes triggering on certain files](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a00ad96bb5a2a679de1f9025d5ec196496326d54) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2498))
- [Fixed a problem in the serialization of the `Query` quantity type](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a013eab878eb684fe2e7120f3b9b0bc96192188c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2493))
- [Fixed problem with quantity description not being shown for EnumEditQuantity and FileEditQuantity](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4ab7fefa8ab1315e39389f2d14bef3f12f8fce54) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2450))
- [Fixed authentication issue with JupyterHub 5](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/bb5fcf23065e3eddad6b0df93082d4a15c8b081d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2466))
- [Fix issue with NorthURL button opening default page if a tab is already opened](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/97895560309812b55e96dd475f0c51805ac1e2f7) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2460))

## 1.3.16 (2025-05-15)

### Changed (4 changes)

- [Added more context to the ConfigBaseModel validation logger warning](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/96b76947d6fc01c1d42defcd868bfe2b333334f9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2429))
- [Removed direct infrastructure dependency from mkdocs macros](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/075653f2b839539e90a07e729d2304268b8f2527) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2366))
- [Use newer version of setuptools](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/112dc2b9a08103da228c12333ae3fd93c870dfc5) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2426))

### Fixed (5 changes)

- [Fixed issue with undefined spectra data](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c2e6601ba5ca611e308ed35adeac9d4f130aca25) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2436))
- [Fixed performance issues with API response for entries with large amounts of search quantities](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/cc3f32a034f57f07aab14ddfd94184106ad09758) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2409))
- [Fixed issue with unit conversion from/to delta units](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/fa071c5df9255c1489ef61a44667cf1105c07289) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2380))
- [Fixed issue with histograms that contain negative numbers](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/5d8438f86461da28e20c62eb72187f85a7ec5442) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2367))
- [Fixed ForwardRef related pydantic error in annotation models](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d324719cf5f0d006dfa51ae452e9685484c802b3) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2403))

### Added (6 changes)

- [Added option for setting the query mode for terms widget and terms menu item](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f744bea733616076ab72bdfba0aa450a4194edb9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2428))
- [Added experimental API endpoint for bulk entries metadata export](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/072952d29940df4d16e010f870af1d5fdb1eb584) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2315))
- [Added new action for launching remote tools from the results table](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e7343bd0de909b3f9a8ae57b48044455a2adfa2d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2279))
- [Added support for negative parser levels](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/203b6e11308bca7f18d7cb7580116c87b0e92bdd) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2362))
- [Added mkdocs function to add schema section and package reference](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/bd2d5c84824cc1a4bbd2c40e1d9c1871c1a41aa6) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2116))
- [Added wheel to the Python package](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/aa13798e378ca1d78d576820eee0f1be0f360aba) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2433))

## 1.3.15 (2025-03-05)

### Fixed (1 change)

- [Fixed user group collection name in MongoDB](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e8daac5fba118c3f2d013f97bc68753cb916754a) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2350))

## 1.3.14 (2025-02-28)

### Added (11 changes)

- [Add nomad distro commit info](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1a03bfa35ecc17b5040b2f1e5ee20b24d544d17e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2329))
- [Resolve "Generic xml parser"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/3f2535f2443892ec71cc2d5881aeb50452052b30) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1655))
- [Resolve "create edges in the workflow"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/fb04f4f163e61b94c0d088863a8ac8afb468cf53) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2318))
- [Added implementation and documentation for DataFrames.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/0696502401fc74a0b460ed3a37acf9f2b378cff9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2211))
- [Implement h5 ref for axes, aux signal](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/7f162a3be13ba454abfd69e2b364739b1b8166d0) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2276))
- [Added support for targeting any NeXus search quantity in aggregations by...](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/bef33475c794afe9328b0e38e9986ae05be78aaf) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2288))
- [Added support for custom atomic labels, radii and colors in the NGL-based structure viewer.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/d26703add00be4c7b57764646aa4bfcf1f272e61) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2302))
- [Added documentation about schema versioning.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/dc5fe24bd18a4564ab0c9c3d73b948152377ef7e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2297))
- [Added annotation for controlling schema availability and naming in the GUI.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/27f4b87bd937cab1163eb2498020c7aef097b805) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2298))
- [Resolve "JSON to JSON model transformer"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/573f53dcbb87be87134a26028cdf181d87dd1303) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1806))
- [Implement wildcard in paths](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/fa64155d93bfb2b39bc016a579d5af65a3f8b6f9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2240))

### Fixed (11 changes)

- [Fixed issue with setting and reading boolean values in queries.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b37db79e23d15470981cfdd6ffdfe701269b5693) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2347))
- [Fixed issue with resolving units when using full storage](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b767f85f45377fd979950970234dc1448d1b4dd7) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2339))
- [Fix properties normalization](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f011e10cc5ad46d8c5d0276d33e429fff9c98f0c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2337))
- [Fixed issues with north config optional values](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/7ecbbb66ad6cb8b44f045a51deb93e7a77c4defa) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2336))
- [Fixed issue with suggestions not being shown by default for terms menu items,...](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/3446265bf31e085b7659bf2446e88052d90a08b2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2330))
- [Updated nomad install documentation and fixed broken links.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/c47bacc6754f7713247c17d1e90a99dd75be6ed0) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2297))
- [Migrated to datacite kernel-4. Fixes #2245.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/5529523b0af7f81004e3501d430f1d441f753590) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2285))
- [Merge branch 'fix-gui-archive-browser-for-readonly-archives' into 'develop'](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/9cba8ca3e63082e275242c2dac62f22f1f9477bf) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2280))
- [bug fix if no tasks](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/561b12a50c27ae3b203ded11466ee17d5351b39c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2270))
- [Fix early return in PubChemPureSubstanceSection to prevent unnecessary processing](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6ab989cef19ad1a5da810c6a7050339dd38b8b23) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2258))
- [Fixes and improvements](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6b6cbc54920fa58e5e31b200ca65d824d380bcfc) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2234))

### Changed (11 changes)

- [Workflow visualizer](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/2db3bee03270dbdd4f1222353925279c385c5208) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2340))
- [Enable ruff rule F401, remove unused import](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/73fb0bb0298b414ec2557cee1de4933593932443) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2325))
- [Patch h5web in postinstall](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e98b6000cb49556c1c1f74cb593c699219604f57) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2319))
- [Merge branch 'pydantic-v2' into 'develop'](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/5b5acfba8fa96aa746d411ecc0464a556a9cc935) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2304))
- [Migrate from Pydantic v1 to v2](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/07b51f2ea57dbcc6c380fea962e4b7e4e5488950) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2304))
- [Resolve "Simplification of System/CompositeSystem/PureSubstance base sections"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4c4a35aa494983b662e41e030416c6b334dbd903) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2300))
- [Resolve "Simplification of System/CompositeSystem/PureSubstance base sections"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/237115a8963f01fe783eb0f3f1f97b5ac6213eda) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2300))
- [Reduced RichTextEdit height for BaseSection description](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/ef14f78325b12bd992f9ad40db323908ed7b1303) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2264))
- [Update jupyter image](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/2a367112e219581d2faea063a064a763df1ffb63) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2252))
- [Respect host and port in nomad config](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1302e4efbfe877bbb0107d260e217271938d9fa9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2243))
- [Restrict httpx version in dependencies](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/543756f04922dd877ad6ccf82c9729019d54ace2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2237))

## 1.3.13 (2024-11-29)

No changes.

## 1.3.12 (2024-11-28)

No changes.

## 1.3.11 (2024-11-21)

### Changed (5 changes)

- [Vendor in pace.min.js and text-mml-chtml.js](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/76ef881b9dc121aacf06673d22e08b2c1bfff6a9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2188))
- [Delayed the loading of example uploads to take place in the processing...](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e0167fa4439ac9be712915ab90f99bc4c62a39b8) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2062))
- [Resolve "move third party integrations to github as plugins"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/0041e0ff2ccdb7360cf4d47b097add6c3837b32d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2124))
- [Make mongo_client import optional](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/45e891baa480d59e0c6a0fc3b923acb18b0bb13f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2187))
- [Upgrade to Python 3.12](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4132472b731354c2fc4c5bbd06a87b516dc957da) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2029))

### Fixed (4 changes)

- [Fixed issues with scatter plot region selection.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1cc85bd37db95f3bbae50e00326afbe3e776836e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2215))
- [Fixed issue with search API not returning all requested data.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a00cb5fd5086c0b92b87a5fcef28a121d6e41a4d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2165))
- [Resolve "SliderEditQuantity not showing"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/10480caf96855a42d1f60f2b1e184b3796708071) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2167))
- [Using the config user and password in CLI when no user is given via](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/46228a14b17dc64c6b291713b533dbde5ba43007) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2089))

### Added (7 changes)

- [Hdf5dataset ref](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/dda4f75b34fdb448a8d72e3a37277c1bcf8b2291) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2194))
- [Implementing writing of h5 ref attrs](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/035beb24dc8d09dc097e2b389ec68bf6e9236539) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2197))
- [Resolve "update mainfile_contents_dict functionality for cvs/xlsx files"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a3eda443927dc5e7c400f32b896b8fa40da52602) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2192))
- [Hdf5dataset attrs](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b9f30c63826f8cdb63669bad4ae60cce55c2d0d9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2169))
- [Added the possibility for defining the menu structure and contents through apps.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e32230ac0a09d14de6330be6c0de131d328e8f16) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2108))
- [Added API entry point.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f7715b890c40b1842e197c3809d20cda066adb0b) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2089))
- [Initial implementation for change base archive editing.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/571a2afa4989897ddbc1242352040df2e087425f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2089))

## 1.3.10 (2024-10-18)

### Fixed (1 change)

- [Resolve "processing published entries fails when using encoding keyword"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f03ad0cd630e5694a4a4142d2d441efa27236d1b) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2140))

## 1.3.9 (2024-10-15)

### Added (4 changes)

- [Added several UK and US unit definitions (e.g. inch, mile, pound) to the GUI unit system.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/0d487496d998ea1bfd91559f7a48960ce728e82d) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2155))

## 1.3.8 (2024-10-11)

### Added (7 changes)

- [Added a new section for adding actions that are triggerable through ELNs.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/061db9b974329eeb8d1bc9c1b77b2e13a9b4e7a7) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2135))
- [Added support for using JMESPath syntax to target data in the search result table columns.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/2fd92ff0118baa21f2e33c5b63c2921b016b6285) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2121))
- [Added molar_mass and monoisotopic_mass to PureSubstanceSection](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1bba058aeb3d940a070dd9115763c8d7a30306ed) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2118))

### Fixed (4 changes)

- [Fixed reference resolution in `ArchiveBrowser` when the referenced data...](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4bbf5a210493653daa06c773f3cbc48d4bfa1a9e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2146))
- [Fixed an issue with creating and reading URLs with queries containing custom quantities.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f49b9fa1b56e38aaa98cef4c33cdc9257b7a6cd9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2137))
- [Disable the Assign DOI button from the UI, if datacite is not enabled in](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/042cf45a920130b82f839798117902ad1d395c62) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2131))

### Changed (3 changes)

- [Remove all deployments from nomad-FAIR](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/553925efd228454aeb737cd2cc00ffb7a0caccdf) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1991))
- [Add lower bounds for packages](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/f95e7b760f9cb577acd3f2d9a85edbae6e560021) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2126))

## 1.3.7 (2024-09-20)

### Added (10 changes)

- [Add group search to upload members dialog](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/20877d677867866afcf98b5cb5da6f9bf246969b) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2044))
- [Resolve "Default shown plots annotation (new feature)"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/3464bdd6f8eeececafa655af2171dfc71c6810c6) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2082))
- [Added explanation about federation and oasis.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/bf8e138279208671cd976b7a6cccb3f649292d4a) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2067))

### Fixed (10 changes)

- [matching parser is failing to parse comments from excel files.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6c36e9fc450143ec7bb9f97327b370b603f74fb1) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2113))
- [extra_fields on elabftw fails to be parsed. new schema on elabftw side causes breaking the parser.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/47cd33837c4ef28c176cb1ffdabfd120adb0fbe8) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2112))
- [Resolve "rework elabftw parser"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b10e1206868aa4d0a6fb4428696f66b8d16236fa) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2095))
- [Resolve "Reference tab has two scrolling bars"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/278e69a9acf54842e27977bc6cf2e518128b8923) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2078))
- [Add context manager to file parser](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/3e981d34ffbd96be2cf703cee0d6c328f5c088f9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2074))
- [Resolve "reading xlsx data in MatchingParser fails"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/26a9be3790c0a2de6dbd7a8ee1f04f431020a46e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2065))

### Changed (9 changes)

- [Use simulation pypi pkgs](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e77ca3add1ca4fe83e77f88e89a6601c1415a610) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2097))
- [Remove simulation-specific packages](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/cc623b116fa7df90a11bf2a791b855f73b90cd4a) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2094))
- [Remove example plugins](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/7f4c4f2854724fb0ab381d179c9126f91f67199e) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2085))
- [Remove remaining submodules](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/4181017b5169a312f3ef70a347a1f277fdd78b58) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2077))
- [Update parsers ref](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/5782f9c171e5b41836a4f110d91035f428257f41) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2070))
- [Remove parsers submodule](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/cc563e81c8b313ea1bbfd7497ed8d57905619760) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2050))

## 1.3.6 (2024-08-29)

### Fixed (2 changes)

- [Resolve "custom schema for tabular parser are failing to process"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/0ce8ec7ba72823cd9ddea6c93d73f2d7380aea06) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1993))
- [Resolve "frontend float quantities validation"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/e842999ccfdcbf3c43fdb53d800b87e788aa8e79) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2039))

### Added (1 change)

- [Added new query visualization below the search bar.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/a571698c3ece95ff5b4e0f93c78f360bd29fc7c6) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2042))

### Changed (1 change)

- [Remove normalizers submodule](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/1925aa71faa4455cd2f446038aed27eaf0c5bd6f) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2041))

## 1.3.5 (2024-08-19)

### Fixed (3 changes)

- [Fix normalization of md errors](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/bb869beaf8f54c365b5fa36801c744f78c500dd2) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2038))
- [Resolve "Bugfix: type conversion in PubChemPureSubstanceSection"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/89e811062c6fda4214e099c7bc131323cea44454) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1997))
- [Fixed an issue with GUI access check not considering the viewer groups.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/330396f791b40728dceb0e46a7dc710ed652d14a) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1994))

### Changed (8 changes)

- [Add simulation workflow plugin](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/34f9b8c07e4e6ea578fb8fb3323baa054a19c97c) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2031))
- [Changes the display nexus card logic to the new way a parser is loaded in and...](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/65155c5b4a4b6438a6a168d9322a6df7c3e4cbe9) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2026))
- [Remove run submodule](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/b573fe26472520f4a4f463b4c91d632db67e0cd1) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2021))
- [Move visible-for-all checkbox to upload overview](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/3f7f323456fa8be990ef8447d2c06660d8bea859) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2005))
- [Keycloak js dependency update](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/aea9daf677d0b23aff62485434c1c6ce6cb56d1b) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/2006))
- [Migrate to Python311](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/ac33b51e5235cfb96e4c5a5d21eef5aaa51b38f1) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1918))
- [Clean up prerelease python packages](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/86c8133aed50b1821252624ac416890f68e0b7b5) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1953))
- [Resolve "Define a template for plots style"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/ad797a0817f1524714c7e52df205ca8590cbcb56) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1956))

### Added (4 changes)

- [Resolve "Preview plot from CLI parse"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/7128244d6cde88eb259cbf41765cc5dd2cc81be0) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1773))
- [Added new plugin entry point type for example uploads.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/05a7e2844d3fea33fa332c6d74998186cb650219) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1988))
- [Resolve "Output of `QueryEditQuantity`"](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/24441b348d8e90860780cc3bf37c0b9581e135f5) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1996))
- [Added a new logarithmic scaling option for histograms and plot axes used in the search interface.](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/commit/6f6bac344d1ad9d7471fa0124b0c4fecc71bcf3b) ([merge request](https://gitlab.mpcdf.mpg.de/nomad-lab/nomad-FAIR/-/merge_requests/1974))

## 1.3.4 (2024-07-11)

### Fixed (16 changes)

- [Fixed the type of allowed_users in the config.](nomad-lab/nomad-FAIR@db004396296d0cace553117acd104d39a704d052) ([merge request](nomad-lab/nomad-FAIR!1960))
- [Resolve "Section reference dialog filters not resetting"](nomad-lab/nomad-FAIR@1892378ab615974350b7f4649ee631d2eaf425d6) ([merge request](nomad-lab/nomad-FAIR!1952))
- [Removed polyfill.io from the GUI.](nomad-lab/nomad-FAIR@075c43d369c565a43fcc4b2fab2a9ab672e99f98) ([merge request](nomad-lab/nomad-FAIR!1951))
- [Resolve "ASAP parser issue"](nomad-lab/nomad-FAIR@70ccc79fa44be7e70e6af14919f12509451c6c23) ([merge request](nomad-lab/nomad-FAIR!1948))
- [Fixed missing horizontal scroll ability on file preview lanes.](nomad-lab/nomad-FAIR@a1599e8923925467333300ba99493d6a497d685f) ([merge request](nomad-lab/nomad-FAIR!1934))
- [Fixed some broken links and missing examples in the docs.](nomad-lab/nomad-FAIR@410154e32b057319d8e4288bda9f66cc4c1d7d7b) ([merge request](nomad-lab/nomad-FAIR!1933))
- [Fixed issue with SI-prefixed units in unit systems.](nomad-lab/nomad-FAIR@a881144b218fad436a8e172d786cd96521e7d3b7) ([merge request](nomad-lab/nomad-FAIR!1913))
- [Resolve "DateEditQuantity and TimeEditQuantity are removed from the app"](nomad-lab/nomad-FAIR@b2db30a37b1ceba06d301bb47a64a7be99e5cf2c) ([merge request](nomad-lab/nomad-FAIR!1894))
- [Resolve "Some datetime related tests fail at night"](nomad-lab/nomad-FAIR@a52d4ce4c800b62b42ec1818d7c35734d4308a85) ([merge request](nomad-lab/nomad-FAIR!1904))
- [Fixed the visibility on the dataset page to show metadata of embargoed data.](nomad-lab/nomad-FAIR@ef5aa12068168488690b08c8ac9fd413b99f15aa) ([merge request](nomad-lab/nomad-FAIR!1911))
- [Fixed an url encoding issue in the h5groove integration.](nomad-lab/nomad-FAIR@b2ae7cfbefc22b685f1dfba912b90630e94a9ebc) ([merge request](nomad-lab/nomad-FAIR!1906))
- [Add label argument in TextFieldWithHelp](nomad-lab/nomad-FAIR@d0c45bfe8258f57b48809f309a697aff3e3ec677) ([merge request](nomad-lab/nomad-FAIR!1902))
- [Fixed handling of missing definitions for dynamic search quantities.](nomad-lab/nomad-FAIR@4639cb233e4ec75de78b0db1388d92ce204df374) ([merge request](nomad-lab/nomad-FAIR!1903))
- [Fixed a problem in juypterhubconfig that was preventing priveledged services.](nomad-lab/nomad-FAIR@9808e817861c9124d00498fd4f551c940b7b50cc) ([merge request](nomad-lab/nomad-FAIR!1876))
- [Changed that uploads shared with all do not show in a users upload list.](nomad-lab/nomad-FAIR@9c135a35ca9bea64acb97da4685c3f0f2e941605) ([merge request](nomad-lab/nomad-FAIR!1876))
- [Fixed the used request version to be compatible with current jupyterhub.](nomad-lab/nomad-FAIR@1e8eac635dfbcc047994fff540e42076fb0a9a06) ([merge request](nomad-lab/nomad-FAIR!1876))

### Added (7 changes)

- [Search edit quantity](nomad-lab/nomad-FAIR@19fcae353e08ad9f1a1829b5ae92862bd9868fd8) ([merge request](nomad-lab/nomad-FAIR!1877))
- [Added jupyter based admin console to k8s deployments.](nomad-lab/nomad-FAIR@02b38910a4e7825d56017d244dd1ae229d0332fb) ([merge request](nomad-lab/nomad-FAIR!1942))
- [Added support for "delta" units that allow comparisons between value ranges...](nomad-lab/nomad-FAIR@ab42c42631d1981a9a66703aea4fd647145a1ba8) ([merge request](nomad-lab/nomad-FAIR!1828))
- [Added RSM to the `results.XRDMethod`](nomad-lab/nomad-FAIR@7715b08b8313c04fa97d12137c560aa2d3ebe374) ([merge request](nomad-lab/nomad-FAIR!1846))
- [Add AI toolkit as a search app.](nomad-lab/nomad-FAIR@6105279fc2317f8e1163039a3b482574dae39189) ([merge request](nomad-lab/nomad-FAIR!1860))
- [Added plugin tutorial to docs](nomad-lab/nomad-FAIR@a5a5d0f6b046a48d4822a9a3fd0c5a80a5f65210) ([merge request](nomad-lab/nomad-FAIR!1890))
- [Added release documentation.](nomad-lab/nomad-FAIR@9f3eae6ceeac89186101bfeb960306a46ff9e814) ([merge request](nomad-lab/nomad-FAIR!1874))

### Changed (5 changes)

- [Merge branch 'migrate-to-openbis' into 'develop'](nomad-lab/nomad-FAIR@c9fc3821ab52407e76fd02b6ff10e582a30ce786) ([merge request](nomad-lab/nomad-FAIR!1924))
- [Update sim workflow schema](nomad-lab/nomad-FAIR@8785c2b66d58a81d66d071d7911f95c2b20a1a08) ([merge request](nomad-lab/nomad-FAIR!1923))
- [Migrated all porosity-related calculations into a separate plugin together with the MOF app.](nomad-lab/nomad-FAIR@8f90a8dbad6008c1533706f8a25694a161a4a61b) ([merge request](nomad-lab/nomad-FAIR!1850))
- [Moved perovskite solar cell schema and app into a plugin.](nomad-lab/nomad-FAIR@0e81daeb47bbdc06cabfeebafe7e6b9e90e6a70c) ([merge request](nomad-lab/nomad-FAIR!1845))
- [Update run schema submodule](nomad-lab/nomad-FAIR@a817baa1631519537c3183cce0b23672e0c5f753) ([merge request](nomad-lab/nomad-FAIR!1914))

## 1.3.3 (2024-06-03)

### Fixed (2 changes)

- [Fixed a problem in juypterhubconfig that was preventing priveledged services.](nomad-lab/nomad-FAIR@9808e817861c9124d00498fd4f551c940b7b50cc) ([merge request](nomad-lab/nomad-FAIR!1876))
- [Fixed issue with wrong example_uploads.yaml path.](nomad-lab/nomad-FAIR@cf82f7d46e003f2af3e794a9851a294f3cd6916c) ([merge request](nomad-lab/nomad-FAIR!1883))

## 1.3.2 (2024-05-31)

### Fixed (1 change)

- [Fixed issue with wrong example_uploads.yaml path.](nomad-lab/nomad-FAIR@cf82f7d46e003f2af3e794a9851a294f3cd6916c) ([merge request](nomad-lab/nomad-FAIR!1883))

## 1.3.1 (2024-05-29)

No changes.

## 1.3.0 (2024-05-28)

### Fixed (67 changes)

- [Fix vasp parser](nomad-lab/nomad-FAIR@0454fc561da9930c205946da7139cc675cb8a7e7) ([merge request](nomad-lab/nomad-FAIR!1873))
- [Fixed image tag in docker-compose examples.](nomad-lab/nomad-FAIR@429714aa72f16705ce606cf7a889b2c55da47785) ([merge request](nomad-lab/nomad-FAIR!1859))
- [Avoid warnings on expected env vars.](nomad-lab/nomad-FAIR@228e7acd8e61bcdeaffa460b69405741b3294294) ([merge request](nomad-lab/nomad-FAIR!1859))
- [Simplified log transfer.](nomad-lab/nomad-FAIR@7f276c147aa674311e0e83007e52bff68018b686) ([merge request](nomad-lab/nomad-FAIR!1801))
- [Resolve "Consistent labels in archive browser and ELNs"](nomad-lab/nomad-FAIR@231526f051e94a250130d665f8f74d2555bcd22c) ([merge request](nomad-lab/nomad-FAIR!1837))
- [Fixed mkdocs search and footer](nomad-lab/nomad-FAIR@b752bb5af5be4c9ea101e2ca668bea1b04e0c8ab) ([merge request](nomad-lab/nomad-FAIR!1838))
- [Fixed integration tests.](nomad-lab/nomad-FAIR@4060e3e0eea70da12998463097a53103272d434f) ([merge request](nomad-lab/nomad-FAIR!1834))
- [Update parser ref](nomad-lab/nomad-FAIR@0bdc8352dd3ec619ec419680ee7ddff02bb26374) ([merge request](nomad-lab/nomad-FAIR!1820))
- [Merge branch...](nomad-lab/nomad-FAIR@8a74a992fcb79835e236474460a0609d90691e57) ([merge request](nomad-lab/nomad-FAIR!1808))
- [Fixed the default argument to required on ArchiveQuery.](nomad-lab/nomad-FAIR@54912fd8e09718e5d66e8bd36fa0f685085130ab) ([merge request](nomad-lab/nomad-FAIR!1804))
- [Fixed the --port argument to admin run app.](nomad-lab/nomad-FAIR@3e0dab277c74e665dd7a8f3b78939dedc52ebb80) ([merge request](nomad-lab/nomad-FAIR!1800))
- [Remove global import of datamodel](nomad-lab/nomad-FAIR@06b83091419b35bf82abbec6f5b8f6dafb01ba3f) ([merge request](nomad-lab/nomad-FAIR!1799))
- [Fix HDF5 ref docs](nomad-lab/nomad-FAIR@1dffd0ee88c1f966ba235ea71a1e88a772604a85) ([merge request](nomad-lab/nomad-FAIR!1795))
- [Fixed wrong archive version suffix default value.](nomad-lab/nomad-FAIR@3baa072d166d3bcfab4a971c4dad61cdd481fcb5) ([merge request](nomad-lab/nomad-FAIR!1792))
- [Resolve "Wrong jupyter upgrade command in docs"](nomad-lab/nomad-FAIR@11c245f64519ed289603aa7dc65b90cebed6f1e6) ([merge request](nomad-lab/nomad-FAIR!1782))
- [Resolve "Booleans are not displayed on overview page"](nomad-lab/nomad-FAIR@38d4825044efb4d1d7ac1384a6f73c8196bde03b) ([merge request](nomad-lab/nomad-FAIR!1753))
- [Fixed issue with relative paths being used in the filesystem config.](nomad-lab/nomad-FAIR@e149c4fc7dbb43abcf727d7c5ad64795e6d4e3db) ([merge request](nomad-lab/nomad-FAIR!1754))
- [Fixed loglevel config to accept string loglevels.](nomad-lab/nomad-FAIR@d3af23cd5411c8aa5d02fb40aef265667cf5c4ed) ([merge request](nomad-lab/nomad-FAIR!1741))
- [Fixed the keycloak image registry used by docker-compose example.](nomad-lab/nomad-FAIR@000b13d68c2d9f18181028ff31b99697fb5e842c) ([merge request](nomad-lab/nomad-FAIR!1736))
- [Added oasis config to nomad helm chart.](nomad-lab/nomad-FAIR@d4bd30e5e70cb13e3019bd92757a931c37b645f8) ([merge request](nomad-lab/nomad-FAIR!1727))
- [Fixed broken error handling on datacite errors.](nomad-lab/nomad-FAIR@4b6833418556a321ff179daff95b3ff55a202fb5) ([merge request](nomad-lab/nomad-FAIR!1727))
- [Upgraded Jupyterhub.](nomad-lab/nomad-FAIR@fb937a3d19ea801b26a3943565286a93a2fef58c) ([merge request](nomad-lab/nomad-FAIR!1677))
- [Resolve "data structure of the .eln file format has changed"](nomad-lab/nomad-FAIR@539193f3fc872d26c28bcf01874b7400315c8875) ([merge request](nomad-lab/nomad-FAIR!1720))
- [Fixed gui data etag.](nomad-lab/nomad-FAIR@c048786a8d97403ebccb05a6fe78334318bf5c00) ([merge request](nomad-lab/nomad-FAIR!1682))
- [Add raw_path_exists method to ClientContext](nomad-lab/nomad-FAIR@68c31600c13bce0bbe3f3a1862bff2c6fe12fb81) ([merge request](nomad-lab/nomad-FAIR!1711))
- [Fixed issue with file preview using incorrect content length for deciding when to stop streaming.](nomad-lab/nomad-FAIR@390cd9eaa5ce4f8bd1560fbe22bff4eb5f94df09) ([merge request](nomad-lab/nomad-FAIR!1691))
- [Resolve "Rendering of SubSection names in Gui"](nomad-lab/nomad-FAIR@66334b8f5b76f0c7f5d65a2f59aed49efd5ed4aa) ([merge request](nomad-lab/nomad-FAIR!1684))
- [Added support to serialize schemas with inner section definitions.](nomad-lab/nomad-FAIR@b9dc902b5cd9d5b00d5a96b222f68fdf16f993e0) ([merge request](nomad-lab/nomad-FAIR!1625))
- [Resolve "plotly config dictionary can't be fed in from normalize function"](nomad-lab/nomad-FAIR@e3d4c8bafc271c77f98f4e2ff3eb0907971e29cb) ([merge request](nomad-lab/nomad-FAIR!1662))
- [Fixed backwards compatibility of recent simulation schema changes.](nomad-lab/nomad-FAIR@1f456807fe1b176e7f6a0bd3a2213cd4cb721cfb) ([merge request](nomad-lab/nomad-FAIR!1665))
- [Fixed wrong type on north.enabled config.](nomad-lab/nomad-FAIR@0c980916a878ef6f1f54c94f68877352bfbaa7be) ([merge request](nomad-lab/nomad-FAIR!1635))
- [Do not lazy load schema plugin](nomad-lab/nomad-FAIR@2bd71598b651469a654f1f42fc6a21f60d6a20db) ([merge request](nomad-lab/nomad-FAIR!1661))
- [Resolve "tabular parser should set the file name correctly"](nomad-lab/nomad-FAIR@33222013664c0eebe4bdba08fddba4127ba692c9) ([merge request](nomad-lab/nomad-FAIR!1652))
- [Resolve "include and exclude the visible and the editable properties"](nomad-lab/nomad-FAIR@ce1de89a347a10bf0943138e91d9e5b1489255fb) ([merge request](nomad-lab/nomad-FAIR!1631))
- [Fix quantum espresso parser](nomad-lab/nomad-FAIR@c3e589dfde91dc1bd29f6525ecb66b0f731d41e5) ([merge request](nomad-lab/nomad-FAIR!1619))
- [Removed `species_at_site` from indexed optimade properties.](nomad-lab/nomad-FAIR@16228696c5516caf6d0547d02df7ff7d0468bb41) ([merge request](nomad-lab/nomad-FAIR!1603))
- [Fixed issue with datetime fields not being updated in the ELN.](nomad-lab/nomad-FAIR@67b7525caade853f6b60baa9022512705788dcc4) ([merge request](nomad-lab/nomad-FAIR!1596))
- [Fix assigment of entry domain](nomad-lab/nomad-FAIR@88bf5e17d3d37b86579f12a8e6eb880e93391d70) ([merge request](nomad-lab/nomad-FAIR!1600))
- [Resolve "wrong margin in subplot"](nomad-lab/nomad-FAIR@d4d8a5a37108fddbc414b614b38d53a73157713b) ([merge request](nomad-lab/nomad-FAIR!1589))
- [content is missing in Labfolder data element](nomad-lab/nomad-FAIR@4dc7e199d777febc3675371ba08da7524e5f6e5c) ([merge request](nomad-lab/nomad-FAIR!1595))
- [Resolve "updating Labfolder api"](nomad-lab/nomad-FAIR@06d44cf301194f0e02e9b07d579d8383d80601ee) ([merge request](nomad-lab/nomad-FAIR!1586))
- [Fixed `to_step` Method `ExperimentStep` Section](nomad-lab/nomad-FAIR@0bef9bed3092d15bd201f4cd345a64e07afc183a) ([merge request](nomad-lab/nomad-FAIR!1584))
- [Fixed wrongly abbreviated author list.](nomad-lab/nomad-FAIR@6d8c46a7fe2082b510f907f98a9b555d7695d8b2) ([merge request](nomad-lab/nomad-FAIR!1585))
- [Update parser ref](nomad-lab/nomad-FAIR@8d102b5cf25575ff4a78837ee54c975961cfa04a) ([merge request](nomad-lab/nomad-FAIR!1580))
- [Fixed issue with large keyword values](nomad-lab/nomad-FAIR@7aa9236d08ecd54b3a510cdd2b698f12db0bb5a7) ([merge request](nomad-lab/nomad-FAIR!1562))
- [Resolve "Setting ReferenceEditQuantity to None in normalie"](nomad-lab/nomad-FAIR@9a8f7763d7046f095de84e6e4c13afd0ad66a37d) ([merge request](nomad-lab/nomad-FAIR!1558))
- [Resolve "Wrong entry type"](nomad-lab/nomad-FAIR@7d928e9a6abe51f5b1fccb67a48d7f5f4111ac6f) ([merge request](nomad-lab/nomad-FAIR!1532))
- [Resolve "Overview page displaying mixed information"](nomad-lab/nomad-FAIR@aa6f3cdd3ddab43f90e86545cc314334c9803274) ([merge request](nomad-lab/nomad-FAIR!1544))
- [Resolve "Cannot plot a Datetime Quantity"](nomad-lab/nomad-FAIR@1ec90b819dc5ab5f9ef5d5e3201f6aef159047ca) ([merge request](nomad-lab/nomad-FAIR!1533))
- [Add additional ruff check to print linting errors to console](nomad-lab/nomad-FAIR@9bef0af7d80ea500adcee907a4164962dd0bcb8c) ([merge request](nomad-lab/nomad-FAIR!1547))
- [Fixed issue with hashtag not being included in query string](nomad-lab/nomad-FAIR@8e19a647d8a03417450fe7fced4d347ccd33a3a4) ([merge request](nomad-lab/nomad-FAIR!1546))
- [Resolve "View Data in the Archive Button in plots does not work"](nomad-lab/nomad-FAIR@eec0fb7a1bb040d92245f61544067b744cbf8579) ([merge request](nomad-lab/nomad-FAIR!1521))
- [Resolve "Some tabular parser modifications to make it more flexible"](nomad-lab/nomad-FAIR@b5a7cd008b6bbdcce9705bf654b125565e0b735f) ([merge request](nomad-lab/nomad-FAIR!1528))
- [Fixed issue with search interface not syncing with URL query string.](nomad-lab/nomad-FAIR@726f8b3ff29c5f281b695b03047896ae7bc3f7f8) ([merge request](nomad-lab/nomad-FAIR!1534))
- [Replace the celery-based example data usage with the ExampleData](nomad-lab/nomad-FAIR@84c8df12d28a5537e2788feb5b8053e7d7476ca3) ([merge request](nomad-lab/nomad-FAIR!1406))
- [Resolve "Incorrect unit for geometry optimization final energy difference"](nomad-lab/nomad-FAIR@9b2ed12329b24f560f26bd59b2c6e4b0ecc329e2) ([merge request](nomad-lab/nomad-FAIR!1527))
- [Fixed issue with periodic table not being shown on some older browsers.](nomad-lab/nomad-FAIR@03b72fcf486b55ec60b4b2f02ddb3dd601524390) ([merge request](nomad-lab/nomad-FAIR!1523))
- [Fixed issue with plots not having enough space for title.](nomad-lab/nomad-FAIR@dd10d0e2d03a069ab44063bad421f25cc3f90d13) ([merge request](nomad-lab/nomad-FAIR!1518))
- [Resolve "Deprecated plot annotation"](nomad-lab/nomad-FAIR@2d2059e3fca372f4c3c0e048eba24930b4514e4e) ([merge request](nomad-lab/nomad-FAIR!1512))
- [Fixed wrong dataset URL in EditMetaDataDialog.](nomad-lab/nomad-FAIR@2ef8d2f5ee402d6c8c8529d1545132d4094daf2c) ([merge request](nomad-lab/nomad-FAIR!1509))
- [Fixed exessively long entry results columns.](nomad-lab/nomad-FAIR@59f15dd92950589e4a0ab13b7747ee9ffe5b3344) ([merge request](nomad-lab/nomad-FAIR!1508))
- [Gromacs cleanup](nomad-lab/nomad-FAIR@938cb3a1a4a85d2c4fadd4f2679a39e7a49406ab) ([merge request](nomad-lab/nomad-FAIR!1505))
- [Fixed missing link to download installation files for oasis with keycloak.](nomad-lab/nomad-FAIR@1a3de09ea48aeadaace0532139458ef48b0901d7) ([merge request](nomad-lab/nomad-FAIR!1497))
- [Update file parser and parser documentation](nomad-lab/nomad-FAIR@c3304ff32a4bd6136d24c905c0bf204a516076e3) ([merge request](nomad-lab/nomad-FAIR!1479))
- [Fixed etags produced by fastapi to work with nginx.](nomad-lab/nomad-FAIR@84a96d3a962106f74444cac24f8ab4efcadbed9e) ([merge request](nomad-lab/nomad-FAIR!1486))
- [Fixes the NOMAD app only running one worker.](nomad-lab/nomad-FAIR@079219e90a6dcdb8013b65f823db9ddf6603fb01) ([merge request](nomad-lab/nomad-FAIR!1477))
- [Fixed missing cache-control and etags.](nomad-lab/nomad-FAIR@a7f236340433ffe62745848e8a411cb4cd2b9d36) ([merge request](nomad-lab/nomad-FAIR!1483))

### Changed (19 changes)

- [Resolve "Consistent labels in archive browser and ELNs"](nomad-lab/nomad-FAIR@b6bad818022a1d89d1bf16d21b22cbe6dfb4677c) ([merge request](nomad-lab/nomad-FAIR!1797))
- [Changed the config mechanism to delay validation until all data is read,...](nomad-lab/nomad-FAIR@cecfc2ad300fc90710bba9973b1041448c27cf94) ([merge request](nomad-lab/nomad-FAIR!1729))
- [Changed the configuration loading logic to support partial updates that are...](nomad-lab/nomad-FAIR@d8ae0409ac2888db12b6b50be51c928c45c919d4) ([merge request](nomad-lab/nomad-FAIR!1716))
- [Remove submodules from dev dependencies](nomad-lab/nomad-FAIR@ae5e3d83c724565c72305bdd980bd8fba0a96520) ([merge request](nomad-lab/nomad-FAIR!1579))
- [Remove Perovskite-Solar-Cell-Database files](nomad-lab/nomad-FAIR@94882cf5e65c1459dda179232eb5437cd4e523a1) ([merge request](nomad-lab/nomad-FAIR!1660))
- [Moved the .gui_configured folder into a subdirectory of the working directory.](nomad-lab/nomad-FAIR@f172443fefaeccff34e2bc5ef129a6f41c7c27b0) ([merge request](nomad-lab/nomad-FAIR!1635))
- [Update parser ref](nomad-lab/nomad-FAIR@db8b6bca75d66a775c61a1b33995c0f91565f657) ([merge request](nomad-lab/nomad-FAIR!1658))
- [Update plugin refs](nomad-lab/nomad-FAIR@84794e15f4df063dd264e178ab7514c0d74008af) ([merge request](nomad-lab/nomad-FAIR!1657))
- [Added config options to disable optional APIs.](nomad-lab/nomad-FAIR@22acbfac017cdf481e1ed0d3bfc3591397891a4a) ([merge request](nomad-lab/nomad-FAIR!1646))
- [Updated the MatID version and increased the MatID clustering size limit.](nomad-lab/nomad-FAIR@ed20ff4a34e1964018349c80dd3f22a11a23f90f) ([merge request](nomad-lab/nomad-FAIR!1636))
- [Merge branch 'fail-format-exit-code-in-ci' into 'develop'](nomad-lab/nomad-FAIR@39f6551e508abc87a2ebcfca6c39effad0c77231) ([merge request](nomad-lab/nomad-FAIR!1618))
- [Merge branch 'update-doc-re-linting' into 'develop'](nomad-lab/nomad-FAIR@082da1b45187033f9b65d4cab42f69584441e7c9) ([merge request](nomad-lab/nomad-FAIR!1568))
- [Merge branch 'documentation-for-formatting' into 'develop'](nomad-lab/nomad-FAIR@c307afdad32109c12888bc0d3e4e86afd8d7bb18) ([merge request](nomad-lab/nomad-FAIR!1613))
- [Merge branch 'auto-format' into 'develop'](nomad-lab/nomad-FAIR@5d5879a4b4f12fec1ebd0d133684190810b6a838) ([merge request](nomad-lab/nomad-FAIR!1610))
- [Merge branch 'add-tabular-description' into 'develop'](nomad-lab/nomad-FAIR@67c960b4491cf01787e96593dd4e36094def9f59) ([merge request](nomad-lab/nomad-FAIR!1612))
- [Merge branch 'fix-mypy' into 'develop'](nomad-lab/nomad-FAIR@6432c8b35bdd43f1c351183139bb5567baa21ae5) ([merge request](nomad-lab/nomad-FAIR!1609))
- [Resolve "Include Perovskite Solar Cells Database Pugin"](nomad-lab/nomad-FAIR@6c2f381bc8b477c364a8f5b9cf115528036da6ab) ([merge request](nomad-lab/nomad-FAIR!1605))
- [Resolve "Create entry with custom schema: filter results ordered by EntryId?"](nomad-lab/nomad-FAIR@f275b37103d58ab605da3879f1652c1b3e58e41e) ([merge request](nomad-lab/nomad-FAIR!1590))
- [Resolve "DataTextParser inheriting from TextParser"](nomad-lab/nomad-FAIR@86d672fcb3c732fa01596a74f37f585a69bb853a) ([merge request](nomad-lab/nomad-FAIR!1530))

### Added (42 changes)

- [Added documentation for the new plugin mechanism.](nomad-lab/nomad-FAIR@e9314d5e5f5b0b0a7e01c6baafbeacc85c9eb339) ([merge request](nomad-lab/nomad-FAIR!1815))
- [Added new plugin mechanism based on entry points.](nomad-lab/nomad-FAIR@11c436392eb150334125d77fc46f01ce1044e212) ([merge request](nomad-lab/nomad-FAIR!1819))
- [Added a new plugin mechanism based on Python entry points.](nomad-lab/nomad-FAIR@9afa5eb8dad0c08876a2700680c34dd0b8043f19) ([merge request](nomad-lab/nomad-FAIR!1803))
- [Resolve "automatic creation and resolution of hdf5 references for large arrays"](nomad-lab/nomad-FAIR@070037d3c2947601419b09bd587bc77329cec0de) ([merge request](nomad-lab/nomad-FAIR!1706))
- [Add workflow visualizer docs](nomad-lab/nomad-FAIR@77f3ebc1586ca62aa219cd30cbd1aa1cecbeb4d4) ([merge request](nomad-lab/nomad-FAIR!1776))
- [Add checkbox to make upload visible for all](nomad-lab/nomad-FAIR@5a8da522327f60e5994a3084af86ab2ef1c44784) ([merge request](nomad-lab/nomad-FAIR!1751))
- [Added documentation for downloading files and data with curl.](nomad-lab/nomad-FAIR@19c408bfea09c865a90752a4866e966c70818cad) ([merge request](nomad-lab/nomad-FAIR!1744))
- [Added download for published upload .zip file.](nomad-lab/nomad-FAIR@1aa3a6ffdb2ef0c02a837d2b20d0268abf7156f5) ([merge request](nomad-lab/nomad-FAIR!1742))
- [Added required parameter to post entry archive download API.](nomad-lab/nomad-FAIR@dea2f24ed74b09b83eecc135c3e4445498e717d7) ([merge request](nomad-lab/nomad-FAIR!1742))
- [Add functionality to convert archives created with the old writer.](nomad-lab/nomad-FAIR@6f57f6557be41a62a71414e7cdd0591ee5cbe29e) ([merge request](nomad-lab/nomad-FAIR!1699))
- [Added possibility to use provide units and labels for scatter plot axes, added...](nomad-lab/nomad-FAIR@accd453c60c6ced89c4c707bb6827d6b65b9b7d8) ([merge request](nomad-lab/nomad-FAIR!1678))
- [Introduce common parser class](nomad-lab/nomad-FAIR@a5f5740f515af14d4fee583736e1a5467bf0411a) ([merge request](nomad-lab/nomad-FAIR!1686))
- [Resolve "Defaul global unit system"](nomad-lab/nomad-FAIR@06509bb4f5ba586f39d039521e1c689f3c97d986) ([merge request](nomad-lab/nomad-FAIR!1597))
- [Added a publish command to the cli.](nomad-lab/nomad-FAIR@f33d0421102161e6230862dfa8bdb1aa8ddd919c) ([merge request](nomad-lab/nomad-FAIR!1679))
- [Converter for json to and from pd dataframe.](nomad-lab/nomad-FAIR@b40e32921573344f90c12fdea2b1dd0c266c1c01) ([merge request](nomad-lab/nomad-FAIR!1628))
- [Add deprecation note on old run schema](nomad-lab/nomad-FAIR@b11bd7a3710e7c5b5246dd92b2139d3726efd87e) ([merge request](nomad-lab/nomad-FAIR!1672))
- [Move run section to plugin](nomad-lab/nomad-FAIR@56c2ed8cdd288939c8a892205892527ba1270865) ([merge request](nomad-lab/nomad-FAIR!1621))
- [Added support for free-text queries and improved search bar UX.](nomad-lab/nomad-FAIR@c10c472ec4df34a655d059a40c9429fa4846af8e) ([merge request](nomad-lab/nomad-FAIR!1615))
- [Added a script to clean up old branches.](nomad-lab/nomad-FAIR@070d09b1edc1b279bfb3287a8a85a5d2abc74a2f) ([merge request](nomad-lab/nomad-FAIR!1617))
- [Added Possibility to Assign Dimensionless Pint Quantity to Unitless Metainfo Quantity](nomad-lab/nomad-FAIR@c9c642bd1483caa4c9236d4f884d34e67423a35c) ([merge request](nomad-lab/nomad-FAIR!1587))
- [Add normalizer_level to ArchiveSection to control order of normalization within sub section.](nomad-lab/nomad-FAIR@3332850f234982d314ccb2a54b8ec686d35cbccd) ([merge request](nomad-lab/nomad-FAIR!1591))
- [Resolve "Make simulation workflow definition as plug-in"](nomad-lab/nomad-FAIR@85898633fcdd3c91901377077a981f274b723279) ([merge request](nomad-lab/nomad-FAIR!1581))
- [Resolve "OpenBIS Integration"](nomad-lab/nomad-FAIR@d2bb1f308aaa50ef853398c84f82bcdeda2271ab) ([merge request](nomad-lab/nomad-FAIR!1543))
- [Add nomad normalizer example plugin](nomad-lab/nomad-FAIR@7be9bc7eb08878ad249adf6a3ebe935c187d520a) ([merge request](nomad-lab/nomad-FAIR!1583))
- [Added support for querying scalar quantities stored in the nexus section.](nomad-lab/nomad-FAIR@8db396c71c66d19100fce4ae929d9640464a3ca0) ([merge request](nomad-lab/nomad-FAIR!1561))
- [Added wrap mode option to the API endpoint for retrieving system geometries.](nomad-lab/nomad-FAIR@6c117c9b2ad9815d7544f355bd4b521be2bc1604) ([merge request](nomad-lab/nomad-FAIR!1576))
- [Added support for controlling unit systems through the configuration file and...](nomad-lab/nomad-FAIR@29e68f032314ccf34d0f01303805cb8c46237b81) ([merge request](nomad-lab/nomad-FAIR!1542))
- [Added an example upload with tutorial and notebook.](nomad-lab/nomad-FAIR@a5ca3008a86919c0e72beb767cc52318e95c9d84) ([merge request](nomad-lab/nomad-FAIR!1545))
- [Add github commit hash](nomad-lab/nomad-FAIR@e1c1519620bb8b5aabbc14183701941ec0e3894d) ([merge request](nomad-lab/nomad-FAIR!1493))
- [Added explanation documentation for processing.](nomad-lab/nomad-FAIR@f8438815f7e151808413186505de18953e071c8b) ([merge request](nomad-lab/nomad-FAIR!1524))
- [Replace pylint with ruff linting](nomad-lab/nomad-FAIR@abd9a8bb1285dac3e3452406bdd12a387a23624f) ([merge request](nomad-lab/nomad-FAIR!1531))
- [Added MOF App.](nomad-lab/nomad-FAIR@82fcf95464b10246e2c3968b934419509443d034) ([merge request](nomad-lab/nomad-FAIR!1537))
- [Provide possibility to return msection from xpath](nomad-lab/nomad-FAIR@ff0f52fe218c2ed284b95db5c25d70cd2d217133) ([merge request](nomad-lab/nomad-FAIR!1535))
- [Added default widget setup for the "calculations" app.](nomad-lab/nomad-FAIR@52497a23c77faf9b6335e65a41c21e87c80a2909) ([merge request](nomad-lab/nomad-FAIR!1510))
- [Add ruff as a linter to replace pycodestyle.](nomad-lab/nomad-FAIR@d8378d742c55eb5947190932704b0a5adaa53a48) ([merge request](nomad-lab/nomad-FAIR!1495))
- [Added the possibility to use custom schemas in apps and search.](nomad-lab/nomad-FAIR@eec68abfd009193f223c79b47366e26cbf7a2a6a) ([merge request](nomad-lab/nomad-FAIR!1500))
- [Resolve "Workflow for chemical reaction steps"](nomad-lab/nomad-FAIR@c0e2b03c8a32650094b243085715967f31c4525a) ([merge request](nomad-lab/nomad-FAIR!1502))
- [Added Base Section Workflow Generation](nomad-lab/nomad-FAIR@85ee603d9fcf6df8a33b6cbbba91824066c2520f) ([merge request](nomad-lab/nomad-FAIR!1455))
- [Add support for negative indexing in msection](nomad-lab/nomad-FAIR@c248d99b7e7e80de34ea1f6ea231966a790d936c) ([merge request](nomad-lab/nomad-FAIR!1487))
- [Resolve "Revising Tabular parser features"](nomad-lab/nomad-FAIR@a30ae5bb6a30feb274a7a3def70d0ec03834cfeb) ([merge request](nomad-lab/nomad-FAIR!1436))
- [Resolve "Implement ordering of simulation workflow tasks by execution time"](nomad-lab/nomad-FAIR@b9560e8a05ee685cff34b4728a6c8277702f2e52) ([merge request](nomad-lab/nomad-FAIR!1474))
- [Add parser matching of excel contents](nomad-lab/nomad-FAIR@35f797bf462cb296e48facdbf96d8d81b85df60d) ([merge request](nomad-lab/nomad-FAIR!1473))

### Removed (1 change)

- [Remove mdtraj](nomad-lab/nomad-FAIR@dcf9ce691bbbb5bb543f5f9506e40d9755345fa1) ([merge request](nomad-lab/nomad-FAIR!1573))

### Added. (1 change)

- [Added metainfo for catalytic properties and an initial app definition for heterogeneous catalysis.](nomad-lab/nomad-FAIR@b366c39e611d49e0b2ffd5279dfb355f872c2174) ([merge request](nomad-lab/nomad-FAIR!1431))

### Added YAML serialization for dashboard export/import. (1 change)

- [Added new input component for configs, started using YAML in dashboard config,...](nomad-lab/nomad-FAIR@203846493553cc50370d17287624f8eb7c31799d) ([merge request](nomad-lab/nomad-FAIR!1484))

## 1.2.1 (2023-09-21)

### Added (8 changes)

- [CLI utility to export archive data.](nomad-lab/nomad-FAIR@88c59a988e2cfafa321466b2aadef70ef16f74ed) ([merge request](nomad-lab/nomad-FAIR!1452))
- [Added Activity ID to ExperiementStep](nomad-lab/nomad-FAIR@1ff3610d114820df24b5cfd7a77415b1707a2893) ([merge request](nomad-lab/nomad-FAIR!1456))
- [Added oasis registration link to the docs.](nomad-lab/nomad-FAIR@3b06ce9a083414da1340be14bbbc48a525436c10) ([merge request](nomad-lab/nomad-FAIR!1450))
- [Added authentication for mongo and elastic use.](nomad-lab/nomad-FAIR@c1f4147dddbaf6def6e831bd924a170a401b7db8) ([merge request](nomad-lab/nomad-FAIR!1332))
- [Added YAML linting plugin for NOMAD schemas.](nomad-lab/nomad-FAIR@d6ae88b6a84a7505d0f85d43a9e12a544c3780a0) ([merge request](nomad-lab/nomad-FAIR!1439))
- [Added System and PureSubstance components for CompositeSystem, added...](nomad-lab/nomad-FAIR@03b23a052eb6d6c2e0cf06596c73b64ec2518f8a) ([merge request](nomad-lab/nomad-FAIR!1385))
- [Added better search filters when searching for custom schemas.](nomad-lab/nomad-FAIR@d97d90e2c993fad1e5a199e6e7d03c36757578f4) ([merge request](nomad-lab/nomad-FAIR!1335))
- [Resolve "Refactoring tabular parser"](nomad-lab/nomad-FAIR@1a68e76252f6da76aab40eff7250d99119d9b8e1) ([merge request](nomad-lab/nomad-FAIR!1377))

### Fixed (13 changes)

- [Update parser](nomad-lab/nomad-FAIR@44043007d27552044407af750d7c45e7c241292b) ([merge request](nomad-lab/nomad-FAIR!1458))
- [Resolve "ReadableIdentifiers Normalizers Fails"](nomad-lab/nomad-FAIR@e9726fac65e8b8882063ab1888e883974ec898a7) ([merge request](nomad-lab/nomad-FAIR!1454))
- [Fixed the condition for maximum numbers of uploads.](nomad-lab/nomad-FAIR@6743000803b8af28481c1eacdd91d2b6a1b5ed96) ([merge request](nomad-lab/nomad-FAIR!1446))
- [Fixed issue with inconsistent upload status icon usage, added more consistent...](nomad-lab/nomad-FAIR@91f57ea304e360f0e58febd59a93c97c26161bf5) ([merge request](nomad-lab/nomad-FAIR!1428))
- [Fixed issues with zero values and redundant API calls in the user defined quantities menu.](nomad-lab/nomad-FAIR@ee5228bfc5d40ecf8385b0f9bcf18c20dad7bf71) ([merge request](nomad-lab/nomad-FAIR!1423))
- [Fixed issue when stripping debug symbols from shared object files.](nomad-lab/nomad-FAIR@6500857b05cea0d9839e3c67b6b12ee269a66832) ([merge request](nomad-lab/nomad-FAIR!1409))
- [Fixed problem with hardcoded jupyter keycloak realm.](nomad-lab/nomad-FAIR@a1ed9d4eec3e45a88868e748afd5b1a6e0a3a1a6) ([merge request](nomad-lab/nomad-FAIR!1407))
- [Fixed issue with loading the custom quantities menu.](nomad-lab/nomad-FAIR@1ea52c685972e58938a735d6a05b54feda2c5ffb) ([merge request](nomad-lab/nomad-FAIR!1420))
- [Fixed issue with reading boolean values from VASP INCAR and OUTCAR files.](nomad-lab/nomad-FAIR@1e47e7e4f43577466a4cbdc6876a56f3aeee20d8) ([merge request](nomad-lab/nomad-FAIR!1419))
- [Update parser ref](nomad-lab/nomad-FAIR@2a333c2ff1e8a431f732fb8561b190a08a6fb938) ([merge request](nomad-lab/nomad-FAIR!1418))
- [Fixing problem with removing archives generated with tabular parser entry mode](nomad-lab/nomad-FAIR@fe7e50447a6254fb2a0c1a15ae625ceb6722739d) ([merge request](nomad-lab/nomad-FAIR!1417))
- [Fixed issue with row action visuals.](nomad-lab/nomad-FAIR@651a2f3c2be27b63eaee10362b76befb99135c79) ([merge request](nomad-lab/nomad-FAIR!1412))
- [Fixed the missing call to generate_docs_artifacts in the setup_dev_env.sh.](nomad-lab/nomad-FAIR@035e1f43745d5422514d06187b675f89e2a39628)

### Changed (8 changes)

- [Removed obsolete elk csv to md script.](nomad-lab/nomad-FAIR@e50dc0fc009815e8c4127cf1fa7c195e6e683e8c) ([merge request](nomad-lab/nomad-FAIR!1332))
- [Command line `--parser` now skips parser matching](nomad-lab/nomad-FAIR@b230af63d027c86828b71e1b7639de9448a9564c) ([merge request](nomad-lab/nomad-FAIR!1233))
- [Improved styling and navigation when browsing nexus files](nomad-lab/nomad-FAIR@5a4184d2cca2ed73d5113de2202d5bd6c8ef1fd6) ([merge request](nomad-lab/nomad-FAIR!1397))
- [Improved the handling of dynamically resolved filters in SearchContext.](nomad-lab/nomad-FAIR@e3248fdaa5a1c7cebae25102f0c464cb1587229d) ([merge request](nomad-lab/nomad-FAIR!1433))
- [Changed the coloring and texts in the upload page to make typical upload...](nomad-lab/nomad-FAIR@8d3a24996f807340227bd73f006b0493a6debe8b) ([merge request](nomad-lab/nomad-FAIR!1425))
- [Updated the documentation on mounting plugins within docker.](nomad-lab/nomad-FAIR@c0b0747a9a0bbcbdb3f6ccf4833a65354dfd7bea) ([merge request](nomad-lab/nomad-FAIR!1422))
- [Refactored periodic table componeng using SVG.](nomad-lab/nomad-FAIR@ad3a56f8ab0a6a2fabcd0ad1f0a9334eaa8a4b53) ([merge request](nomad-lab/nomad-FAIR!1415))
- [Removed outdated information about using parsers from the parser projects.](nomad-lab/nomad-FAIR@b41131f576d3fd3574d0e81f67b3c180ebec48b3)

## 1.2.0 (2023-09-21)

### Added (39 changes)

- [CLI utility to export archive data.](nomad-lab/nomad-FAIR@88c59a988e2cfafa321466b2aadef70ef16f74ed) ([merge request](nomad-lab/nomad-FAIR!1452))
- [Added Activity ID to ExperiementStep](nomad-lab/nomad-FAIR@1ff3610d114820df24b5cfd7a77415b1707a2893) ([merge request](nomad-lab/nomad-FAIR!1456))
- [Added oasis registration link to the docs.](nomad-lab/nomad-FAIR@3b06ce9a083414da1340be14bbbc48a525436c10) ([merge request](nomad-lab/nomad-FAIR!1450))
- [Added authentication for mongo and elastic use.](nomad-lab/nomad-FAIR@c1f4147dddbaf6def6e831bd924a170a401b7db8) ([merge request](nomad-lab/nomad-FAIR!1332))
- [Added YAML linting plugin for NOMAD schemas.](nomad-lab/nomad-FAIR@d6ae88b6a84a7505d0f85d43a9e12a544c3780a0) ([merge request](nomad-lab/nomad-FAIR!1439))
- [Added System and PureSubstance components for CompositeSystem, added...](nomad-lab/nomad-FAIR@03b23a052eb6d6c2e0cf06596c73b64ec2518f8a) ([merge request](nomad-lab/nomad-FAIR!1385))
- [Added better search filters when searching for custom schemas.](nomad-lab/nomad-FAIR@d97d90e2c993fad1e5a199e6e7d03c36757578f4) ([merge request](nomad-lab/nomad-FAIR!1335))
- [Resolve "Refactoring tabular parser"](nomad-lab/nomad-FAIR@1a68e76252f6da76aab40eff7250d99119d9b8e1) ([merge request](nomad-lab/nomad-FAIR!1377))
- [Added compression suport to quantum expresso mainfiles.](nomad-lab/nomad-FAIR@cb7326b31a3f13fcb9c204c2c45c68144becec80) ([merge request](nomad-lab/nomad-FAIR!1401))
- [Added a new code contribution guide to the docs.](nomad-lab/nomad-FAIR@5fef1c2eabdc6fcf14f32236ec38d4b57e269793) ([merge request](nomad-lab/nomad-FAIR!1396))
- [Added helm config options for plugins and normalize.](nomad-lab/nomad-FAIR@73cdacf80c4b1abc54911dfb706791b6fd400be2) ([merge request](nomad-lab/nomad-FAIR!1393))
- [Added more documentation on data, schemas, and plugins.](nomad-lab/nomad-FAIR@b0d590503e3eea3dff0e30672257cd4c0ab2318d) ([merge request](nomad-lab/nomad-FAIR!1390))
- [Added new archive config options to helm chart.](nomad-lab/nomad-FAIR@25de71a4e72252e3236c5a6131c69deddf81a2a1) ([merge request](nomad-lab/nomad-FAIR!1251))
- [Added a soap normalizer normalizer.](nomad-lab/nomad-FAIR@f6c5064f27056e70f920d8aeb5efc2ed4629124f) ([merge request](nomad-lab/nomad-FAIR!1391))
- [Added list of all parsers to the reference documentation.](nomad-lab/nomad-FAIR@1c066432dd3fae88aa610c4d97397e66744456d8) ([merge request](nomad-lab/nomad-FAIR!1268))
- [Added authors column to the processing table](nomad-lab/nomad-FAIR@bc2e6c7a5b0429772f273f20d647a70c6d186c6d) ([merge request](nomad-lab/nomad-FAIR!1360))
- [Hight props for RichTextEditor](nomad-lab/nomad-FAIR@121ddcab7885105235dc0dc42903e1a1a8506562) ([merge request](nomad-lab/nomad-FAIR!1334))
- [1. Foldable list of edit quantities](nomad-lab/nomad-FAIR@c77e2bff38999f55c9baf8fa80c536d526fd2ded) ([merge request](nomad-lab/nomad-FAIR!1337))
- [Added normalizer for analyzing porous systems.](nomad-lab/nomad-FAIR@304c411013b2b3820188ef40846bdb0cab6b2d11) ([merge request](nomad-lab/nomad-FAIR!1294))
- [Added new file formats for the API endpoint used for downloading atomistic...](nomad-lab/nomad-FAIR@55eb60873b65e5f6238cb0ff7dfb7006b192db0f) ([merge request](nomad-lab/nomad-FAIR!1353))
- [Implement eos fit normalization #1439](nomad-lab/nomad-FAIR@37195bd45cfcbe99c6df219da2eb38a5b6873e04) ([merge request](nomad-lab/nomad-FAIR!1319))
- [Resolve "Replace workflow with workflow2"](nomad-lab/nomad-FAIR@39891c46b06d0a123884f109df369336600e01ba) ([merge request](nomad-lab/nomad-FAIR!1261))
- [Resolve "Documentation for third party integration"](nomad-lab/nomad-FAIR@eace52d4ff78041e733ca1bedc0dce26c8edfcad) ([merge request](nomad-lab/nomad-FAIR!1301))
- [Add new metainfo `native tiers` in `run` under `electrons_representation` and...](nomad-lab/nomad-FAIR@2e9d138de277e73918ed43aa38696a6a14c00f54) ([merge request](nomad-lab/nomad-FAIR!1283))
- [Added the functionality to optionally show the section label instead of full path of the reference](nomad-lab/nomad-FAIR@ec6a8c1322c7cf05dea29b34a4a2067c2b0b87d8) ([merge request](nomad-lab/nomad-FAIR!1215))
- [Add parser level in interface](nomad-lab/nomad-FAIR@a395dc59ed9e5e141b624a569419ba7ab0dc6a8c) ([merge request](nomad-lab/nomad-FAIR!1298))
- [Added basic topologies under results.material.topology for all simulation entries.](nomad-lab/nomad-FAIR@7b7e6b08f707618b0a594580a960d3bc8f01eca2) ([merge request](nomad-lab/nomad-FAIR!1284))
- [Added warning when deleting uploads or entries that are referenced](nomad-lab/nomad-FAIR@13a65f94a3100ac90f1c335cfbf7486d50eda582) ([merge request](nomad-lab/nomad-FAIR!1218))
- [Added basic information, such as mass fraction and atomic fraction, of each element in results.](nomad-lab/nomad-FAIR@f42b7caa6df07ed23f05c24ac2b0ae91e099d2dc) ([merge request](nomad-lab/nomad-FAIR!1190))
- [Added ELN base section for reading and storing structure files.](nomad-lab/nomad-FAIR@22f5dc1af80822be500137422379a395eda1b1ae) ([merge request](nomad-lab/nomad-FAIR!1229))
- [Added new metainfo for dimensionality and building_block](nomad-lab/nomad-FAIR@45e95123f4c9d51a9b57f4a796d3faa52845494f) ([merge request](nomad-lab/nomad-FAIR!1259))
- [Added config switch for generating new timestamps.](nomad-lab/nomad-FAIR@3b97832c704d6e80e09bb0741eedef5f741ad8b1) ([merge request](nomad-lab/nomad-FAIR!1250))
- [Added automatic subsystem detection.](nomad-lab/nomad-FAIR@08ef593bc3f0e184f97a79953c185dc34ddd2f59) ([merge request](nomad-lab/nomad-FAIR!1242))
- [Added config option to fallback to a different archive version.](nomad-lab/nomad-FAIR@94d8412531ced89e8cc5f15c032e117bbfcc74ec) ([merge request](nomad-lab/nomad-FAIR!1248))
- [Added new metainfo for storing the level of the XC functional.](nomad-lab/nomad-FAIR@0f6515828ca3c373f438db8e4e0e6e4c9d628911) ([merge request](nomad-lab/nomad-FAIR!1246))
- [Added function m_setdefault for more conveniently storing data inside archives.](nomad-lab/nomad-FAIR@91b838cc2f1c32796478f005e0ec0b0aa7bba9ce) ([merge request](nomad-lab/nomad-FAIR!1232))
- [Added plugin support for NOMAD Oasis.](nomad-lab/nomad-FAIR@9622d9192e7680b0077c10ad8e46f7b00c2e746f) ([merge request](nomad-lab/nomad-FAIR!1058))
- [Added configurable list of normalizers.](nomad-lab/nomad-FAIR@8852644fd258ceb2ab080d41a793bab18ea1b704) ([merge request](nomad-lab/nomad-FAIR!1058))
- [Added plugin base models and schema, parser models.](nomad-lab/nomad-FAIR@488873666807960fbe57eab0e72091203aaffa24) ([merge request](nomad-lab/nomad-FAIR!1058))

### Fixed (45 changes)

- [Update parser](nomad-lab/nomad-FAIR@44043007d27552044407af750d7c45e7c241292b) ([merge request](nomad-lab/nomad-FAIR!1458))
- [Resolve "ReadableIdentifiers Normalizers Fails"](nomad-lab/nomad-FAIR@e9726fac65e8b8882063ab1888e883974ec898a7) ([merge request](nomad-lab/nomad-FAIR!1454))
- [Fixed the condition for maximum numbers of uploads.](nomad-lab/nomad-FAIR@6743000803b8af28481c1eacdd91d2b6a1b5ed96) ([merge request](nomad-lab/nomad-FAIR!1446))
- [Fixed issue with inconsistent upload status icon usage, added more consistent...](nomad-lab/nomad-FAIR@91f57ea304e360f0e58febd59a93c97c26161bf5) ([merge request](nomad-lab/nomad-FAIR!1428))
- [Fixed issues with zero values and redundant API calls in the user defined quantities menu.](nomad-lab/nomad-FAIR@ee5228bfc5d40ecf8385b0f9bcf18c20dad7bf71) ([merge request](nomad-lab/nomad-FAIR!1423))
- [Fixed issue when stripping debug symbols from shared object files.](nomad-lab/nomad-FAIR@6500857b05cea0d9839e3c67b6b12ee269a66832) ([merge request](nomad-lab/nomad-FAIR!1409))
- [Fixed problem with hardcoded jupyter keycloak realm.](nomad-lab/nomad-FAIR@a1ed9d4eec3e45a88868e748afd5b1a6e0a3a1a6) ([merge request](nomad-lab/nomad-FAIR!1407))
- [Fixed issue with loading the custom quantities menu.](nomad-lab/nomad-FAIR@1ea52c685972e58938a735d6a05b54feda2c5ffb) ([merge request](nomad-lab/nomad-FAIR!1420))
- [Fixed issue with reading boolean values from VASP INCAR and OUTCAR files.](nomad-lab/nomad-FAIR@1e47e7e4f43577466a4cbdc6876a56f3aeee20d8) ([merge request](nomad-lab/nomad-FAIR!1419))
- [Update parser ref](nomad-lab/nomad-FAIR@2a333c2ff1e8a431f732fb8561b190a08a6fb938) ([merge request](nomad-lab/nomad-FAIR!1418))
- [Fixing problem with removing archives generated with tabular parser entry mode](nomad-lab/nomad-FAIR@fe7e50447a6254fb2a0c1a15ae625ceb6722739d) ([merge request](nomad-lab/nomad-FAIR!1417))
- [Fixed issue with row action visuals.](nomad-lab/nomad-FAIR@651a2f3c2be27b63eaee10362b76befb99135c79) ([merge request](nomad-lab/nomad-FAIR!1412))
- [Fixed the missing call to generate_docs_artifacts in the setup_dev_env.sh.](nomad-lab/nomad-FAIR@035e1f43745d5422514d06187b675f89e2a39628)
- [Fixed broken links in the documentation.](nomad-lab/nomad-FAIR@5caceed75a964f2c10884c15e93acfa33a9f1a6a) ([merge request](nomad-lab/nomad-FAIR!1403))
- [Fixed issue with extra_fields in extracted elabFTW `.eln` file](nomad-lab/nomad-FAIR@5d61bcdf909104fa4d0bbce9739e6113dbadcc33) ([merge request](nomad-lab/nomad-FAIR!1383))
- [Fix some visual problems](nomad-lab/nomad-FAIR@50d749a0d73e2e45377ef301f6af9e16ca5431f7) ([merge request](nomad-lab/nomad-FAIR!1375))
- [The plot axis units should firstly follow the eln.defaultDisplayUnit annotation if exists](nomad-lab/nomad-FAIR@5d9af6ee5741652653002e377f4101c7dfc49aaf) ([merge request](nomad-lab/nomad-FAIR!1346))
- [Allow uploading multiple files to a list-shaped FileEditQuantity](nomad-lab/nomad-FAIR@5d81efa1415880c5d4f3380aa15f3d934ddb1503) ([merge request](nomad-lab/nomad-FAIR!1333))
- [Cleaning up the method how the version has been determined during the CI/CD build process](nomad-lab/nomad-FAIR@1fdc3c139e21a219f02365ab4b9e35f5244a8522) ([merge request](nomad-lab/nomad-FAIR!1354))
- [Fixed ASE warnings about get_number_of_atoms and the usage of arrays with...](nomad-lab/nomad-FAIR@be65aa650968921eb422f8b80bd3a78988d4cb6b) ([merge request](nomad-lab/nomad-FAIR!1352))
- [Adjust matching parser regex for new mopac version](nomad-lab/nomad-FAIR@09c0cf4a8ea41225f63bcc01d3067606e1e96e45) ([merge request](nomad-lab/nomad-FAIR!1349))
- [Address performance issue in matching parser with child archives](nomad-lab/nomad-FAIR@539278167e7a046c67f633bdb39c09b635989652) ([merge request](nomad-lab/nomad-FAIR!1343))
- [Resolve "Referenced section is not working"](nomad-lab/nomad-FAIR@9b9e694d23947e895d7cb2723a985b951bcb71fc) ([merge request](nomad-lab/nomad-FAIR!1338))
- [Fixed issue with the conventional cell reported by MatID.](nomad-lab/nomad-FAIR@c243c9db2c702e6f039facfb0d26cac838d0281e) ([merge request](nomad-lab/nomad-FAIR!1336))
- [Visually indicate entries with processing errors](nomad-lab/nomad-FAIR@cdd7339688e73b7472b87931a67d75e831ac182a) ([merge request](nomad-lab/nomad-FAIR!1318))
- [Fixed the staging deployment.](nomad-lab/nomad-FAIR@9a8aea95adc0404498fdceea72787cfcca276f0c) ([merge request](nomad-lab/nomad-FAIR!1329))
- [Uploads Page, delete button is disabled when selecting all uploads](nomad-lab/nomad-FAIR@f7da75ce50a615961196dccd9bb6047befe0cfdf) ([merge request](nomad-lab/nomad-FAIR!1313))
- [fix saving files including both definitions and data](nomad-lab/nomad-FAIR@1d5752318457582889efaacf703970a6d4c6b2fb) ([merge request](nomad-lab/nomad-FAIR!1231))
- [Fixed the staging deployment.](nomad-lab/nomad-FAIR@449ca9e95b82ac542915e6d2cab17d8fb0ecf137) ([merge request](nomad-lab/nomad-FAIR!1324))
- [Fixed the name of the fhi parser.](nomad-lab/nomad-FAIR@f2d8e82aa65324ef4b96101372e4f3e9a6e54a83) ([merge request](nomad-lab/nomad-FAIR!1326))
- [Fix creation of d3 elements](nomad-lab/nomad-FAIR@7fef1551a25758957198168eeca5e3a2bb94b317) ([merge request](nomad-lab/nomad-FAIR!1320))
- [Indicate there are multiple co-authors in the upload list.](nomad-lab/nomad-FAIR@a1edfcc8fa27200ad409d59a277c52066f7cb9ad) ([merge request](nomad-lab/nomad-FAIR!1303))
- [Only the main author should be allowed to remove an upload](nomad-lab/nomad-FAIR@0207b89588eb0382e9f3ef4738f9e9d9b4dc43a9) ([merge request](nomad-lab/nomad-FAIR!1291))
- [Update parser refs](nomad-lab/nomad-FAIR@6a6f8cd601f3e95ba957dd4a6b21110d9e4bf068) ([merge request](nomad-lab/nomad-FAIR!1300))
- [Various minor fixes](nomad-lab/nomad-FAIR@75d40012aa236dddd5ed4c183fe6f923249fd717) ([merge request](nomad-lab/nomad-FAIR!1296))
- [Processing fixes](nomad-lab/nomad-FAIR@a7ffc56c479c16c5fe7bd93cc9d07888bbfce950) ([merge request](nomad-lab/nomad-FAIR!1280))
- [Remove Duplicated items in archive browser](nomad-lab/nomad-FAIR@a5601fc12085c0c28b6586774ba7fe4ff7f15d61) ([merge request](nomad-lab/nomad-FAIR!1254))
- [Fix cp2k, abinit and phonopy parsers](nomad-lab/nomad-FAIR@065afe432faa2ef61093b8f6f6ff5f4abff00634) ([merge request](nomad-lab/nomad-FAIR!1265))
- [Implement new workflow visualizer](nomad-lab/nomad-FAIR@763f3547763c94b8dc44484017068e12fa6f1ac5) ([merge request](nomad-lab/nomad-FAIR!1211))
- [Fix RichText editor style in non editable mode](nomad-lab/nomad-FAIR@fadb0387c562648a5b6182c82232db28edc3221d) ([merge request](nomad-lab/nomad-FAIR!1240))
- [Fixed javascript error on north page.](nomad-lab/nomad-FAIR@08d4615cef69446e4f31f98dec2f265b4bbbf94c) ([merge request](nomad-lab/nomad-FAIR!1247))
- [Fix the lane computing](nomad-lab/nomad-FAIR@ec095372cadf9aaf55ec60faa5c7eb0a073e2f4d) ([merge request](nomad-lab/nomad-FAIR!1243))
- [Fix a bug in deployment regarding loading North tools](nomad-lab/nomad-FAIR@885bdd3e4e66154aa9e59730bf5ea4f3a51b8e6c) ([merge request](nomad-lab/nomad-FAIR!1241))
- [Fix phonopy and lobster parser](nomad-lab/nomad-FAIR@ed380695fee5d507ad941eb1274a7c867a3508e4) ([merge request](nomad-lab/nomad-FAIR!1238))
- [Fixed file table in parser about dialog.](nomad-lab/nomad-FAIR@1cb47611b8aad8fcb04fedf61bc2925f4e0a1a8f) ([merge request](nomad-lab/nomad-FAIR!1058))

### Changed (26 changes)

- [Removed obsolete elk csv to md script.](nomad-lab/nomad-FAIR@e50dc0fc009815e8c4127cf1fa7c195e6e683e8c) ([merge request](nomad-lab/nomad-FAIR!1332))
- [Command line `--parser` now skips parser matching](nomad-lab/nomad-FAIR@b230af63d027c86828b71e1b7639de9448a9564c) ([merge request](nomad-lab/nomad-FAIR!1233))
- [Improved styling and navigation when browsing nexus files](nomad-lab/nomad-FAIR@5a4184d2cca2ed73d5113de2202d5bd6c8ef1fd6) ([merge request](nomad-lab/nomad-FAIR!1397))
- [Improved the handling of dynamically resolved filters in SearchContext.](nomad-lab/nomad-FAIR@e3248fdaa5a1c7cebae25102f0c464cb1587229d) ([merge request](nomad-lab/nomad-FAIR!1433))
- [Changed the coloring and texts in the upload page to make typical upload...](nomad-lab/nomad-FAIR@8d3a24996f807340227bd73f006b0493a6debe8b) ([merge request](nomad-lab/nomad-FAIR!1425))
- [Updated the documentation on mounting plugins within docker.](nomad-lab/nomad-FAIR@c0b0747a9a0bbcbdb3f6ccf4833a65354dfd7bea) ([merge request](nomad-lab/nomad-FAIR!1422))
- [Refactored periodic table componeng using SVG.](nomad-lab/nomad-FAIR@ad3a56f8ab0a6a2fabcd0ad1f0a9334eaa8a4b53) ([merge request](nomad-lab/nomad-FAIR!1415))
- [Removed outdated information about using parsers from the parser projects.](nomad-lab/nomad-FAIR@b41131f576d3fd3574d0e81f67b3c180ebec48b3)
- [Changed dependency versions to increase compatibility.](nomad-lab/nomad-FAIR@13e051c104ec8cb4af12507eb260413fb5cc562e) ([merge request](nomad-lab/nomad-FAIR!1402))
- [Changed the version of scikit-learn for better compatibility.](nomad-lab/nomad-FAIR@74f2f767d71012b7d977996084fe82831a9be7c9) ([merge request](nomad-lab/nomad-FAIR!1399))
- [Changed xarray requirements to allow install in colab.](nomad-lab/nomad-FAIR@f15d45debb617e748e487fdad9ba37b67ae0fbb2) ([merge request](nomad-lab/nomad-FAIR!1395))
- [Fixed indexing of NaN in dynamic search properties.](nomad-lab/nomad-FAIR@f7f01bd60a7aee57b69783d6d2bc6e9b7ea2bac2) ([merge request](nomad-lab/nomad-FAIR!1339))
- [Changed version requirements on some dependencies.](nomad-lab/nomad-FAIR@70d1d5a726690e45dc75a5bb25e1aaf26ca691bd) ([merge request](nomad-lab/nomad-FAIR!1384))
- [Update dependencies for parallel test execution with pytest-xdist](nomad-lab/nomad-FAIR@dcf8842b36ae525a06e790654cd97b964f235a13) ([merge request](nomad-lab/nomad-FAIR!1376))
- [Added detailed breadcrumbs](nomad-lab/nomad-FAIR@f5c6519c28b2a669e5be2afe621eb2400ec125ec) ([merge request](nomad-lab/nomad-FAIR!1361))
- [Changed the documentation structure.](nomad-lab/nomad-FAIR@780e7d49b2afac9b2276a902ae0e7c6b7122bf57) ([merge request](nomad-lab/nomad-FAIR!1268))
- [allow navigation to entries with processing errors](nomad-lab/nomad-FAIR@96749a1981deb48797fde775741b21e09670567b) ([merge request](nomad-lab/nomad-FAIR!1327))
- [Changed the versions of pyproject.toml dependencies.](nomad-lab/nomad-FAIR@6b98f085a996311dfc89640d9ffd766aa3298892) ([merge request](nomad-lab/nomad-FAIR!1350))
- [Removed the old material_library ELN example build-in schema.](nomad-lab/nomad-FAIR@6b68dc8e6b1946ebcfaad2999a474c7c2a61373e) ([merge request](nomad-lab/nomad-FAIR!1350))
- [Changed the used jupyterlab image in NORTH.](nomad-lab/nomad-FAIR@daa4f98cd89d653e7ba23dcda9605a9a2d1836cc) ([merge request](nomad-lab/nomad-FAIR!1324))
- [Update parsers ref](nomad-lab/nomad-FAIR@842a11f3fea23d0e73d79acc58fe75cfad372909) ([merge request](nomad-lab/nomad-FAIR!1328))
- [Moved ELN Base Sections](nomad-lab/nomad-FAIR@62d3a60cfd222a16ebd08c2c73be02e898007360) ([merge request](nomad-lab/nomad-FAIR!1295))
- [Make table actions always visible.](nomad-lab/nomad-FAIR@fbaa9786da8a372aef148ce521cf7c8e58069055) ([merge request](nomad-lab/nomad-FAIR!1302))
- [Changed entry_type and entry_name for simulations](nomad-lab/nomad-FAIR@8b0a5d721a6c714fe06ff3ce741f2237d9d0c5a2) ([merge request](nomad-lab/nomad-FAIR!1066))
- [Removed the generation of gui artifacts for development.](nomad-lab/nomad-FAIR@2d9d1c8b1cff0aa28bef8e3b8da6aa026e649880) ([merge request](nomad-lab/nomad-FAIR!1058))
- [Changed gui artifact generation to happen at app start.](nomad-lab/nomad-FAIR@a488b4700b9125be44daff78368f6575e1335e6e) ([merge request](nomad-lab/nomad-FAIR!1058))

### New feature (1 change)

- [Add service "logtransfer" to submit logs generated at a NOMAD OASIS to the central unit (opt-in). ](nomad-lab/nomad-FAIR@cb14c6932b44d304fd8d15e9a0abbf1ba18ae8e7) ([merge request](nomad-lab/nomad-FAIR!894))

### Fixed. (1 change)

- [Fixed docker build: added explicit npm installation.](nomad-lab/nomad-FAIR@6e3fd5f92885f8dbb30d54b7d1e359479b3bfcf2) ([merge request](nomad-lab/nomad-FAIR!1358))

### Feature (2 changes)

- [Duplicate functionality for entries.](nomad-lab/nomad-FAIR@d57f1e2f74b2eb1e969b3133ee87d850e3c8ebc6) ([merge request](nomad-lab/nomad-FAIR!1312))
- [Show upload progress when uploading a file in FileEditQuantity](nomad-lab/nomad-FAIR@aa26e28b71ed785e84d01b4ae99a361839969b7c) ([merge request](nomad-lab/nomad-FAIR!1305))

### Fix (1 change)

- [Fixed the version of the datascience-notebook image NORTH tool.](nomad-lab/nomad-FAIR@6f469ca3b57a1c5b0ba3dabc907d6e356e2208fb) ([merge request](nomad-lab/nomad-FAIR!1276))

## 1.1.9 (2023-03-31)

### Added (10 changes)

- [Added a checkbox to avoid accidentally delete mainfile's folders](nomad-lab/nomad-FAIR@0d138d5b79cdad7fd64bceafd87bdeaa2c6bed68) ([merge request](nomad-lab/nomad-FAIR!1154))
- [Added overwrite dialog for existing files](nomad-lab/nomad-FAIR@d69d719ce6d158c0eb209e730f3f133f0ee4c1c5) ([merge request](nomad-lab/nomad-FAIR!1197))
- [Added example script that printsout metainfo metrics.](nomad-lab/nomad-FAIR@a0ba5ec249e8626bf027cb171140ec501a89489d) ([merge request](nomad-lab/nomad-FAIR!1186))
- [Update parser submodule](nomad-lab/nomad-FAIR@99f350714fea29233e2f9ad9eb16a1d30dbda313) ([merge request](nomad-lab/nomad-FAIR!1170))
- [Added github actions to move PRs to gitlab.](nomad-lab/nomad-FAIR@e2522ee7739d46b1fa800dcc23fcdafbe3363bc1) ([merge request](nomad-lab/nomad-FAIR!1167))
- [Added entry mode to follow the same workflow as the column/row modes in tabular parser](nomad-lab/nomad-FAIR@e9c409b8f0d4b1baa8f1babe4f83ca8fecae5e3c) ([merge request](nomad-lab/nomad-FAIR!1148))
- [Added MaterialCardFormula for entries without structure.](nomad-lab/nomad-FAIR@ae7dc0835a84f6451f17283edbb28b6a3ee7383e) ([merge request](nomad-lab/nomad-FAIR!1142))
- [Added `root` mode to the tabular parser to enable more generalized usage of mixed column/row modes](nomad-lab/nomad-FAIR@12488c1ff2dfca572a4ab686de86529d157977df) ([merge request](nomad-lab/nomad-FAIR!1116))
- [Link to the plot examples was added to the corresponding documentation](nomad-lab/nomad-FAIR@ae4ae61403e1a1ccb78c62b27a08f566b9d7c4b7) ([merge request](nomad-lab/nomad-FAIR!1120))
- [Added docker image build instructions to the docs.](nomad-lab/nomad-FAIR@a471bafb3c77ebe7b55bf3dab75ff3bba045d2fa) ([merge request](nomad-lab/nomad-FAIR!1147))

### Changed (6 changes)

- [Changed docs and pyproject to require Python 3.9.](nomad-lab/nomad-FAIR@64f6bcadb1deddf94b3a5618689e4ee3fe300667) ([merge request](nomad-lab/nomad-FAIR!1201))
- [Improved the metainfo search visuals and performance.](nomad-lab/nomad-FAIR@56658e712b8f252ddc1aff1021091440d05127f0) ([merge request](nomad-lab/nomad-FAIR!1127))
- [Changed the icon used for navigating to a particular entry/upload/dataset.](nomad-lab/nomad-FAIR@296519efd1e6eb6b04182cd8fde0f0992667d230) ([merge request](nomad-lab/nomad-FAIR!1150))
- [1. Show upload name column by default in section select dialog](nomad-lab/nomad-FAIR@8fcf6107762d2852909d4e36cea3b8b9ecf5e8eb) ([merge request](nomad-lab/nomad-FAIR!1151))
- [Disabled optimade respone validation.](nomad-lab/nomad-FAIR@582a90c77558b10cc1bf6574b2c0f6d051119645) ([merge request](nomad-lab/nomad-FAIR!1153))
- [Improved the custom quantities search menu layout and search functionality.](nomad-lab/nomad-FAIR@0c0cf1ccfdd50dbf2aa139104c20a3313b14319c) ([merge request](nomad-lab/nomad-FAIR!1144))

### Fixed (15 changes)

- [Fixed Circular referencing](nomad-lab/nomad-FAIR@f8dcc7999cfa14fdfcb63e7f2ca32f47a94760e6) ([merge request](nomad-lab/nomad-FAIR!1165))
- [Fixed that created but otherwise failed DOIs are not saved.](nomad-lab/nomad-FAIR@bc3fb1fd96fd705cb8f731f823b78f0e075ba75c) ([merge request](nomad-lab/nomad-FAIR!1194))
- [Fixed processing with reuse_parser set to False.](nomad-lab/nomad-FAIR@75031ccb574c697574d743af0b9019196cd47385) ([merge request](nomad-lab/nomad-FAIR!1193))
- [Fixed issue with unit system selection not being read correctly from configuration file.](nomad-lab/nomad-FAIR@476427b8a17e928c4dd2cd6dac06b10935046ce8) ([merge request](nomad-lab/nomad-FAIR!1192))
- [Disable unsupported float point number types on Windows](nomad-lab/nomad-FAIR@0794724a9c571246d1bbdfc442e92157b6a688f5) ([merge request](nomad-lab/nomad-FAIR!1146))
- [Rename bad parser test file](nomad-lab/nomad-FAIR@fad1c6b07e2adcde63908506c5e8aca33fd3e9b5) ([merge request](nomad-lab/nomad-FAIR!1169))
- [Fixed issue in reading deeply nested config file settings.](nomad-lab/nomad-FAIR@782a382e84d1509f7d7634672cf7123c5aaa2273) ([merge request](nomad-lab/nomad-FAIR!1164))
- [Adjust phonopy matcher regex](nomad-lab/nomad-FAIR@0144ed4a7de8ba4452a5e7151535b0ba0a7c7099) ([merge request](nomad-lab/nomad-FAIR!1160))
- [Fixed that metainfo deserialization can overwrite derived values.](nomad-lab/nomad-FAIR@a7e3d45020fac57707cce81115682446aeddaeee) ([merge request](nomad-lab/nomad-FAIR!1159))
- [Fixed is_admin overwrite in OasisUserManagement.](nomad-lab/nomad-FAIR@ef5ab815658d6b6829473ed628a36779733fe6a8) ([merge request](nomad-lab/nomad-FAIR!1159))
- [Fixed issues in upload name not updating correctly after editing.](nomad-lab/nomad-FAIR@aad7fcb63e196136d54e1328140cab26a4209efc) ([merge request](nomad-lab/nomad-FAIR!1156))
- [Fixed data_file referencing of tabular_parser when creating new entries from GUI](nomad-lab/nomad-FAIR@222b4d814c051eebd82f722326951af1709f7430) ([merge request](nomad-lab/nomad-FAIR!1155))
- [Fixed issue with UI allowing terms aggregations for incompatible quantities.](nomad-lab/nomad-FAIR@6e7be3c8d6de76de60b8b6c4e0315138f77d6be4) ([merge request](nomad-lab/nomad-FAIR!1152))
- [Minor fix workflow normalizer](nomad-lab/nomad-FAIR@28e494c0f0884f25b637c202a4e9b7d41b4d4325) ([merge request](nomad-lab/nomad-FAIR!1149))
- [Add new columns and fix some problems with the datatable](nomad-lab/nomad-FAIR@a6e9790c3d48731536182092b879fcda4122f3ec) ([merge request](nomad-lab/nomad-FAIR!1138))

## 1.1.8 (2023-03-10)

### Fixed (9 changes)

- [Fixed a bug in handling UI load errors.](nomad-lab/nomad-FAIR@bc01f359fbd55048fff0c7213ba42ae53451add3) ([merge request](nomad-lab/nomad-FAIR!1143))
- [Add parsers](nomad-lab/nomad-FAIR@b1c177f75cd72dfabaea6137d93375357857b3a6) ([merge request](nomad-lab/nomad-FAIR!1136))
- [Fixed the backend test batch to actually show the coverage.](nomad-lab/nomad-FAIR@532eba1e0ae44f56a93a9580b11ac44d02fa49e5) ([merge request](nomad-lab/nomad-FAIR!1131))
- [Check existance of `m_proxy_value` for references.](nomad-lab/nomad-FAIR@79040365fd370257941f663b92a40984da3bd67b) ([merge request](nomad-lab/nomad-FAIR!1123))
- [Minor fix workflow visualizer](nomad-lab/nomad-FAIR@1b0154166bf24b17a701e91564140e1fdfaf0d7f) ([merge request](nomad-lab/nomad-FAIR!1121))
- [ability to reference to the other uploads properly](nomad-lab/nomad-FAIR@8befc8efe92218aa63a63c562ac8057de54e5ba2) ([merge request](nomad-lab/nomad-FAIR!1070))
- [Added the staging url](nomad-lab/nomad-FAIR@5a0b31a7cff12106a28f0fc02bba7fda764d4708) ([merge request](nomad-lab/nomad-FAIR!1086))
- [Workflow fix](nomad-lab/nomad-FAIR@75170b392d779cf9a27880fd83433c3d47a85b37) ([merge request](nomad-lab/nomad-FAIR!1113))
- [Fixed the availability of the beta/test snack in the UI.](nomad-lab/nomad-FAIR@a3f29510858bf6ba6056f1c52af177e70d24eacc) ([merge request](nomad-lab/nomad-FAIR!1134))

### Added (10 changes)

- [Added additional html sanitization before using html on the UI.](nomad-lab/nomad-FAIR@1f00ee39dd664090a3c0467c81dc7adb48c32e39) ([merge request](nomad-lab/nomad-FAIR!1140))
- [Added Green's functions into electronic properties.](nomad-lab/nomad-FAIR@105c975e35c2a9d179a007a016263e2db6b89448) ([merge request](nomad-lab/nomad-FAIR!1065))
- [Appending similar quantities (column names) to the appropriate subsection in Tabular_Parser](nomad-lab/nomad-FAIR@46a3bb8ca9a8798d9b37fe4ad242b7b459c18551) ([merge request](nomad-lab/nomad-FAIR!1088))
- [Added Feature for Creating Workflow in ELN](nomad-lab/nomad-FAIR@09e6c80bde3519d11cb42f85139adf1febf5c484) ([merge request](nomad-lab/nomad-FAIR!1101))
- [Added hyperlink to entry in scatter plot widget and hover details](nomad-lab/nomad-FAIR@6a8e55f9dee79aed241273d82aeb9d58016ee82d) ([merge request](nomad-lab/nomad-FAIR!1126))
- [Added an HDF5Reference type to the metainfo.](nomad-lab/nomad-FAIR@26de816576d73d05d4a2db79433abc6c4db610f4) ([merge request](nomad-lab/nomad-FAIR!1040))
- [Added better description of our code documentation practices.](nomad-lab/nomad-FAIR@eab55e797c21dcc8a2df68b451fb6bbc5f08fe60) ([merge request](nomad-lab/nomad-FAIR!1129))
- [Added properties eln annotation to customize the appearance and the permissions](nomad-lab/nomad-FAIR@87debf8136b58bb16d57ec8e7a492797504e60b1) ([merge request](nomad-lab/nomad-FAIR!1093))
- [Adding ReferenceEditQuantity Field to LabfolderProject allowing for further customization](nomad-lab/nomad-FAIR@c1c8adf0b1d8316e7cfb7bd7e505b46a202ca819) ([merge request](nomad-lab/nomad-FAIR!1025))
- [Added the possibility to define plots in the custom schemas that use scalar...](nomad-lab/nomad-FAIR@aba802ce34061d27c7bdf9863d30c0aa6605fd0f) ([merge request](nomad-lab/nomad-FAIR!1110))

### Changed (3 changes)

- [Changed the branding of the app and docs.](nomad-lab/nomad-FAIR@c8de145b9f95b9ef44fb511410256be17f375e0f) ([merge request](nomad-lab/nomad-FAIR!1135))
- [Upgraded nomad-lab to Python 3.9 and bumped a few dependency version.](nomad-lab/nomad-FAIR@5cb6d4a46c5ac6c4809e66cc288dee32a68721de) ([merge request](nomad-lab/nomad-FAIR!1125))
- [Changed the config to use Pydantic models for UI aspects.](nomad-lab/nomad-FAIR@de1ed6ffb8db168f6f7ae4f6e9addd59879f552f) ([merge request](nomad-lab/nomad-FAIR!1104))

## 1.1.7 (2023-02-20)

### Added (18 changes)

- [Added a config switch to enable disable north tools.](nomad-lab/nomad-FAIR@37d9b6db1e8044ae28476488e8c3ae1896e149ad) ([merge request](nomad-lab/nomad-FAIR!1099))
- [Refactor NORTH and Jupyterhub deployment. Add a new north API endpoint. Mount...](nomad-lab/nomad-FAIR@b0e28fa7fd3f47fcd538f33bd84bad659ee6549a) ([merge request](nomad-lab/nomad-FAIR!996))
- [Added categories to create entry build-in schemas.](nomad-lab/nomad-FAIR@6a2b96f58c29a09364523c1d140c13c6a9cf24b0) ([merge request](nomad-lab/nomad-FAIR!1085))
- [Added support for slice notation when choosing axis quantities in the plot annotation.](nomad-lab/nomad-FAIR@1fedce5e2a310fde2d304aa38262cbc79adeb289) ([merge request](nomad-lab/nomad-FAIR!1073))
- [New workflow visualizer](nomad-lab/nomad-FAIR@e02bfb3cc5fdcc5eb3beda6910a204e84fde7f5f) ([merge request](nomad-lab/nomad-FAIR!957))
- [Added pagination feature for long sections in the archive browser.](nomad-lab/nomad-FAIR@b13839fdaa1bdd111b2cd32e35a7887ce65db816) ([merge request](nomad-lab/nomad-FAIR!1081))
- [Added categories to the example uploads dialog.](nomad-lab/nomad-FAIR@994e272b478682f25fbdc5b8748ef09764a3ac8f) ([merge request](nomad-lab/nomad-FAIR!1080))
- [Added a new 3D structure viewer based on NGL.](nomad-lab/nomad-FAIR@91e8cd67247b5881b842df14014929098abce0df) ([merge request](nomad-lab/nomad-FAIR!776))
- [Dialog to create entry](nomad-lab/nomad-FAIR@5602071ffb659700360983e2ca7b27105de391b9) ([merge request](nomad-lab/nomad-FAIR!1032))
- [Added ELN Base Sections](nomad-lab/nomad-FAIR@4895124d43de7f3fe57b5c97281e3e86676cab4a) ([merge request](nomad-lab/nomad-FAIR!1037))
- [Ability to create and modify child classes from referenceEditQuantity](nomad-lab/nomad-FAIR@70ec0c443ea2cbf2481c162215283981b714b0b5) ([merge request](nomad-lab/nomad-FAIR!1019))
- [Added possibility for changing the page title to distinguish OASIS installations.](nomad-lab/nomad-FAIR@3604c83813251df4ec9c7daee021ba98cb7baca3) ([merge request](nomad-lab/nomad-FAIR!1056))
- [Added automatic push of develop to github.](nomad-lab/nomad-FAIR@e806087e77f8c6bcbeea9e42c218d0b377be3c6e) ([merge request](nomad-lab/nomad-FAIR!1047))
- [Added a to_ase function to simulation system Atoms section.](nomad-lab/nomad-FAIR@f97425cc835a08f4809a676af246c8ad8e2ea54f) ([merge request](nomad-lab/nomad-FAIR!1027))
- [Added a automatically generated changelog to our git.](nomad-lab/nomad-FAIR@fe795eff97145e0e28d61ca840cbb3069217d24a) ([merge request](nomad-lab/nomad-FAIR!1016))
- [Added a glossary to the documentation.](nomad-lab/nomad-FAIR@aa42834ed51b3d231a8edc58b00ea1f2dddc21f5) ([merge request](nomad-lab/nomad-FAIR!1024))
- [Added dropdown list to select from sections.](nomad-lab/nomad-FAIR@a332abed1318f60078463e2ee71ae68b2086ef1a) ([merge request](nomad-lab/nomad-FAIR!992))
- [Implement extended matching of h5 files.](nomad-lab/nomad-FAIR@96372aa26a0675a0bbae99b8e5059f5e0f1a5f3a) ([merge request](nomad-lab/nomad-FAIR!1023))

### Fixed (18 changes)

- [Fixed wrong property inheritance base section order in UI metainfo.](nomad-lab/nomad-FAIR@9cbd6d9c37d21f06cf9fc3f5d9fbde746d7873b3) ([merge request](nomad-lab/nomad-FAIR!1098))
- [Fixed the build-in schema entry create to use the right definitions.](nomad-lab/nomad-FAIR@8db7bb1be8ab502f4aef178107f50d27297123a2) ([merge request](nomad-lab/nomad-FAIR!1095))
- [Fixed direct navigation of reference edit quantity by disabling it.](nomad-lab/nomad-FAIR@b6ba106989192407954d7846f13abfe13fde431d) ([merge request](nomad-lab/nomad-FAIR!1095))
- [Fixed bi-directional referencing in ELNs.](nomad-lab/nomad-FAIR@b5f8c7ac5f120943f5c235e3ee216424b26f0188) ([merge request](nomad-lab/nomad-FAIR!1095))
- [Resolve "quickfix: typo in scripts/build_sdist.sh"](nomad-lab/nomad-FAIR@0abcb2a53011529d628074203e4b6a67a5fbdf25) ([merge request](nomad-lab/nomad-FAIR!1090))
- [Fixed incorrect URL reported in the API call dialogs.](nomad-lab/nomad-FAIR@1f58dd2f3271c95829867933255bbb11c2540e61) ([merge request](nomad-lab/nomad-FAIR!1089))
- [Resolve the reference to a data defined together the schema in the same file](nomad-lab/nomad-FAIR@94a58aeaf49970b8bc9f8b1ef9605b790e125d31) ([merge request](nomad-lab/nomad-FAIR!1079))
- [ability to reference all with m_def not only data](nomad-lab/nomad-FAIR@d56a3645ed3515a2e63231d93ca013b4b6be3316) ([merge request](nomad-lab/nomad-FAIR!1079))
- [only fetch those entries which are processed](nomad-lab/nomad-FAIR@029e5ead244382330856fc541c4ad3c9d5d2e3c2) ([merge request](nomad-lab/nomad-FAIR!1079))
- [normalize path in url](nomad-lab/nomad-FAIR@d6fbcec4bc58b20910ea283dd3c6c1b741866b9d) ([merge request](nomad-lab/nomad-FAIR!1079))
- [URLEditQuantity is added as a string eln_component to annotations](nomad-lab/nomad-FAIR@a5a916b32358bfa4648ce7899e5dd0cf59186a40) ([merge request](nomad-lab/nomad-FAIR!1062))
- [Fixed issues with the developers setup of nomad.](nomad-lab/nomad-FAIR@d76fc7e061fb6a1662cfa13da6519dde51345ffa) ([merge request](nomad-lab/nomad-FAIR!1064))
- [Fixed endless recursion in metainfo browser.](nomad-lab/nomad-FAIR@1443063a8b3a8df36975204c12d23ba51b9962e2) ([merge request](nomad-lab/nomad-FAIR!1059))
- [Fixed the README batches and changed pipeline to push tags to github.](nomad-lab/nomad-FAIR@8c42e102387b2d0c5f0a55fd368b266306b705f1) ([merge request](nomad-lab/nomad-FAIR!1057))
- [Fixed issue with optimade filter menu performing premature API calls.](nomad-lab/nomad-FAIR@7c4ad789591ce6a30cb6236825798e440711ce21) ([merge request](nomad-lab/nomad-FAIR!1048))
- [Fixed generation of duplicated subsections on updating an entry that triggers tabular parser](nomad-lab/nomad-FAIR@e91176fe2a197f9d8450e29dc8b599f669ecad1d) ([merge request](nomad-lab/nomad-FAIR!1033))
- [Fix the select feature of the Datatable component](nomad-lab/nomad-FAIR@8fd52ed7bccc14c13c6c83d90e3a931df3cf9d25) ([merge request](nomad-lab/nomad-FAIR!1060))
- [Datafile quantity containing `tabular_parser` annotation can be referenced](nomad-lab/nomad-FAIR@8ad191bf7f37b9c1286200127f6f5b2f0cf3701a) ([merge request](nomad-lab/nomad-FAIR!1017))

### Changed (2 changes)

- [Small UX changes to the upload interface.](nomad-lab/nomad-FAIR@5e18b0e12d4ce4d608a5cd68efbeb02ee2d51f04) ([merge request](nomad-lab/nomad-FAIR!1085))
- [Refactored the overview UI code to optimise loading performance.](nomad-lab/nomad-FAIR@161051655e8b9406197247ed943e027fdd13771b) ([merge request](nomad-lab/nomad-FAIR!1075))

### Feature (1 change)

- [Adds an ELN schema that allows to create entries that clone data from a labfolder project.](nomad-lab/nomad-FAIR@166a8aaa65f63bf368453365689e1dab577fc94d) ([merge request](nomad-lab/nomad-FAIR!948))

## 1.1.6 (2022-12-23)

### Fixed (1 change)

- [Fixed failing plot annotation valiation.](nomad-lab/nomad-FAIR@3cd5d0635566d9882e1c44cd922e113dfbeb1292) ([merge request](nomad-lab/nomad-FAIR!1005))

### Feature (1 change)

- [Added a way to define formal models for metainfo annotations.](nomad-lab/nomad-FAIR@6a84b55e17dd5cd41cb5ba571f2451d528dbe6a0) ([merge request](nomad-lab/nomad-FAIR!990))

### Added (1 change)

- [Replaced config object with pydantic models.](nomad-lab/nomad-FAIR@b9c1e023d95d14fd337b98a7ac895f505d4715a9) ([merge request](nomad-lab/nomad-FAIR!981))
