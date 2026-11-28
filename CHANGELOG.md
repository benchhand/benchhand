# Changelog

## Unreleased

- Add an optional per-agent `tier` (cheap, standard, strong), a cheap-tier guard that limits `cheap` to an allowlist (`cad-clerk`), the `cad-clerk` agent for mechanical work, and `install.py --tier-model` to render a model per harness. Without it, generated profiles are unchanged.
- Add a version-2 manufacturing-intent record with explicit use case, requirements versus preferences/assumptions/questions, per-part components/material/process routes, capability and material profiles, deterministic selection, assembly operations, process-dispatched checks, evidence, findings, impact and gated acceptance. The sourced starter material library is typical-only; actual capability, allowables and physical evidence remain project-specific.
- Require explicit `migrate.py` migration for existing version-1 project records before gated actions or new record-writing builds. Version-1 records remain readable with unknown intent; historical seals remain inspectable. New design freezes and releases are blocked when material/process selection or other required facts are unknown. Transparent functions require a traceable purchased transparent part or identified transparent stock on a suitable route, never see-through FDM.
- Extend fits/stack-up, assembly access/play and static FEA to bind results to recorded configurations and evidence. Worst-case governs collision/binding/lost function/critical fit; RSS requires justified assumptions. Geometry, plans and simulation do not establish physical performance or regulatory certification; no CAM/toolpaths or equipment operation are provided.
- Add pinned, development-only mypy. CI checks every changed/new feature production module against an incremental baseline and strictly checks new pure modules; see `cad-mfg-intent/U8F-notes.md` for scope and results.

## [0.2.0](https://github.com/benchhand/benchhand/compare/v0.1.1...v0.2.0) (2026-11-28)


### Features

* add the manufacturing-intent work to the CAD skills ([bfe83e3](https://github.com/benchhand/benchhand/commit/bfe83e33c990454ef3ba4b3b13e055be3eafede5))
* add the manufacturing-intent work to the CAD skills ([b0bce41](https://github.com/benchhand/benchhand/commit/b0bce4109f446543fe18d8f32892751fab8250e7))
* **cad-analysis:** add mode: stress, the two-mesh stress study ([#236](https://github.com/benchhand/benchhand/issues/236)) ([ff80b7c](https://github.com/benchhand/benchhand/commit/ff80b7cdb7942410dd70a197ecfcaa0e13d5ecce))
* **cad-analysis:** add the two-mesh stress study comparison ([#220](https://github.com/benchhand/benchhand/issues/220)) ([8b77551](https://github.com/benchhand/benchhand/commit/8b77551a1b12f579e0262d31f00b272fef0c274c))
* **cad-analysis:** derive the stop reaction from the modelled contact ([4f16a38](https://github.com/benchhand/benchhand/commit/4f16a38e366b969a450a9818ac8201111f87bfb2))
* **cad-analysis:** fast FEA mode with displacement, stiffness and a labelled p95, and no stress figure ([#206](https://github.com/benchhand/benchhand/issues/206)) ([79cb5cb](https://github.com/benchhand/benchhand/commit/79cb5cbb6e8d8d08386b6b415eabea944336a56c))
* **cad-analysis:** flag a form stand-in for metals in the near-support strength check ([#298](https://github.com/benchhand/benchhand/issues/298)) ([1826e04](https://github.com/benchhand/benchhand/commit/1826e0458bb207880ae453fe5707e5ff2ec0a9e9))
* **cad-analysis:** gate near-support strength on sourced elongation ([#290](https://github.com/benchhand/benchhand/issues/290)) ([c029ef9](https://github.com/benchhand/benchhand/commit/c029ef9225bc967b8fa23af6c397e335aa7a8442)), closes [#209](https://github.com/benchhand/benchhand/issues/209)
* **cad-analysis:** keep the full stress tensor and read the element connectivity ([#259](https://github.com/benchhand/benchhand/issues/259)) ([3fdf6e4](https://github.com/benchhand/benchhand/commit/3fdf6e40b1d6363c8c4a52630307152358c67907))
* **cad-analysis:** linearise stress along a stress classification line ([#261](https://github.com/benchhand/benchhand/issues/261)) ([24be800](https://github.com/benchhand/benchhand/commit/24be800400328c7b6ae270de4e7b31b33158c9fe))
* **cad-analysis:** read FEA strength from the two-mesh stress study ([#256](https://github.com/benchhand/benchhand/issues/256)) ([a850fb1](https://github.com/benchhand/benchhand/commit/a850fb1e02cd222d0ba6893e3d9aac1178800b65))
* **cad-analysis:** record mesh validity in every FEA result and never solve an invalid mesh ([43ad440](https://github.com/benchhand/benchhand/commit/43ad4409862294bde388d2003bbc05e2a94d6918))
* **cad-analysis:** refuse an FEA load below the modelled stop reaction before it solves ([#186](https://github.com/benchhand/benchhand/issues/186)) ([ce1f8d7](https://github.com/benchhand/benchhand/commit/ce1f8d78322d20ddf9590d890532f7a44fab7aff))
* **cad-analysis:** wire linearised strength near supports into the stress study ([#266](https://github.com/benchhand/benchhand/issues/266)) ([7e43d58](https://github.com/benchhand/benchhand/commit/7e43d58bf8aba8e1a73f5037bdd31ef67e83057e))
* **cad-design:** gate printer calibration behind CAD_CALIBRATION, off for release ([#310](https://github.com/benchhand/benchhand/issues/310)) ([6df1d0c](https://github.com/benchhand/benchhand/commit/6df1d0c32f324e51f36d4b59987aef46e5615ab7))
* **cad-design:** let form_check pass a packet that is not assessed ([#315](https://github.com/benchhand/benchhand/issues/315)) ([6f1c7ca](https://github.com/benchhand/benchhand/commit/6f1c7ca3344d8c1c2feb7a2e17b667b24d46c1ad))
* **cad-design:** order the not_assessed outcome in combine and the packet result ([#314](https://github.com/benchhand/benchhand/issues/314)) ([e10fbc9](https://github.com/benchhand/benchhand/commit/e10fbc9e8d174e5a071b3a2a599ef382fcd5cf4c))
* **cad-design:** read FDM limits through the guideline resolver, graded by service class ([#272](https://github.com/benchhand/benchhand/issues/272)) ([d7fc236](https://github.com/benchhand/benchhand/commit/d7fc23625b117eb0623be4b4b7e7f3db13ed6882))
* **cad-fits:** check a stack's sliding clearance against the guideline minimum ([#289](https://github.com/benchhand/benchhand/issues/289)) ([8b68948](https://github.com/benchhand/benchhand/commit/8b68948b6ef44b989d3ddc1bca3b5952af7499f3))
* **cad-flow:** enforce the project-start profile in validate, run and the commit check (2/2, supersedes [#115](https://github.com/benchhand/benchhand/issues/115)) ([#316](https://github.com/benchhand/benchhand/issues/316)) ([1cfcf6c](https://github.com/benchhand/benchhand/commit/1cfcf6c5d62d2f2207d46d98a32059134da0b6ce))
* **cad-flow:** project-start profile with accepted-default provenance (1/2, supersedes [#115](https://github.com/benchhand/benchhand/issues/115)) ([#307](https://github.com/benchhand/benchhand/issues/307)) ([1903d0a](https://github.com/benchhand/benchhand/commit/1903d0a0895cac795f17d4d6c15357bb35c332ff))
* **cad-model:** GLB-STEP agreement and declared dimensions as cad.readback outcomes ([#239](https://github.com/benchhand/benchhand/issues/239)) ([9f4234e](https://github.com/benchhand/benchhand/commit/9f4234e54558467b8ea2e45321371e67570af652))
* **cad-model:** independent GLB-STEP agreement and declared-dimension checks ([#42](https://github.com/benchhand/benchhand/issues/42)) ([75f7e72](https://github.com/benchhand/benchhand/commit/75f7e72213afc236083faa505333f69388e140d1))
* **cad-model:** judge an agreement occurrence unknown when its reference mesh strays from the surface ([#138](https://github.com/benchhand/benchhand/issues/138)) ([ff2b715](https://github.com/benchhand/benchhand/commit/ff2b715cbe560a2758273babdb0bbe6eb54a96f9))
* **cad-model:** write the part colour into part.3mf as basematerials ([addef39](https://github.com/benchhand/benchhand/commit/addef3947ebef5d8b3ffb733a4244b8f5041c7c8))
* **cad-motion:** write AP242 geometric validation properties in STEP ([e78159a](https://github.com/benchhand/benchhand/commit/e78159a73ecf459b2e8ad201da617d3bf503df60))
* **cad-recheck:** let stamp take a hw directory and commit a stamped verify project ([#161](https://github.com/benchhand/benchhand/issues/161)) ([07289c8](https://github.com/benchhand/benchhand/commit/07289c85f69551eab8c1d7bb2bf506fe953e63ab))
* **cad-recheck:** require a gate decision record when flow.stage advances past the stamp ([#242](https://github.com/benchhand/benchhand/issues/242)) ([68fad8f](https://github.com/benchhand/benchhand/commit/68fad8f8f6beacf6babd8ab28226b2139730b533))
* **cad-record:** a failed seal means stale, and the producer reruns ([#226](https://github.com/benchhand/benchhand/issues/226)) ([5ee37d9](https://github.com/benchhand/benchhand/commit/5ee37d91bbb070aa8f36c8032c249d9895c82035))
* **cad-record:** add a Click helper that builds options from a JSON Schema config ([18948f9](https://github.com/benchhand/benchhand/commit/18948f9929dfbe8d6eafaf93ddbb66545336b9f8))
* **cad-record:** add a discrete nozzle-diameter set to guideline limits ([#265](https://github.com/benchhand/benchhand/issues/265)) ([3190c6a](https://github.com/benchhand/benchhand/commit/3190c6a8acb8258ad270077e82aba5985145d76c))
* **cad-record:** add bounded qty_clearance_per_side and qty_interference_per_side ([c33b191](https://github.com/benchhand/benchhand/commit/c33b191208dd50283a3e91037b131d8349d62dda))
* **cad-record:** add manufacturing guideline sets with layered resolution and staleness ([#222](https://github.com/benchhand/benchhand/issues/222)) ([f47cc75](https://github.com/benchhand/benchhand/commit/f47cc75a2604b509abccdb7094a48eadf25b0d05))
* **cad-record:** add qty_clearance_per_side and qty_interference_per_side ([#224](https://github.com/benchhand/benchhand/issues/224)) ([c33b191](https://github.com/benchhand/benchhand/commit/c33b191208dd50283a3e91037b131d8349d62dda))
* **cad-record:** add the not_assessed outcome to the manufacturing schema ([#311](https://github.com/benchhand/benchhand/issues/311)) ([e986237](https://github.com/benchhand/benchhand/commit/e9862372b92b0b8ae203b01962e2252069aaf75f))
* **cad-record:** an optional elongation gauge and an fu/fy ratio on material properties ([#288](https://github.com/benchhand/benchhand/issues/288)) ([ce4aec2](https://github.com/benchhand/benchhand/commit/ce4aec297a4cd0ed4c473720a156f44f6643674f))
* **cad-record:** bound min_feature and min_hole and restrict each qty_* to its dimension's units ([#221](https://github.com/benchhand/benchhand/issues/221)) ([b6adfda](https://github.com/benchhand/benchhand/commit/b6adfdae7c8ab825c8deb766eadbbd9f026a7f77))
* **cad-record:** check subject occurrences against their part, and add check_documents ([#254](https://github.com/benchhand/benchhand/issues/254)) ([cbd5718](https://github.com/benchhand/benchhand/commit/cbd571819dac11e9f4a8cb072a8a914df3746867))
* **cad-record:** check subject occurrences against their part; add check_documents ([cbd5718](https://github.com/benchhand/benchhand/commit/cbd571819dac11e9f4a8cb072a8a914df3746867))
* **cad-record:** content-addressed store under hw/.cad/cache ([#114](https://github.com/benchhand/benchhand/issues/114)) ([8cb6317](https://github.com/benchhand/benchhand/commit/8cb6317cbbc694825e0cc1eeb9c280097a6f2b61))
* **cad-record:** defaults, hard bounds and plausibility bands for every tunable schema field ([a489259](https://github.com/benchhand/benchhand/commit/a489259bc8f8ef8db6815d68a7a7f8481a93bc27))
* **cad-record:** engagement_length_mm on fits and heat-set inserts ([6b03da7](https://github.com/benchhand/benchhand/commit/6b03da73a43746b257f03cd03aea18df4ce6d5d3))
* **cad-record:** evidence.py run, a fail-fast runner over the producer matrix ([b76596b](https://github.com/benchhand/benchhand/commit/b76596bba94e98d70a49c9851716736effd52441))
* **cad-record:** guided machine profile check and write ([#304](https://github.com/benchhand/benchhand/issues/304)) ([f53ba2c](https://github.com/benchhand/benchhand/commit/f53ba2cc014fa71164d6ae730e173eb628a02c52))
* **cad-record:** keep pre-existing equipment out of the released product ([#337](https://github.com/benchhand/benchhand/issues/337)) ([2e840b6](https://github.com/benchhand/benchhand/commit/2e840b649092de0ef68b696cacf018030116acd4))
* **cad-record:** MADR 4.0.0 decision records, the only decision format ([#110](https://github.com/benchhand/benchhand/issues/110)) ([1a7efb7](https://github.com/benchhand/benchhand/commit/1a7efb7e5e77ff3e60849e51f2f5d2949eb1bb37))
* **cad-record:** make releasing the same revision idempotent ([#223](https://github.com/benchhand/benchhand/issues/223)) ([7bb7f02](https://github.com/benchhand/benchhand/commit/7bb7f02a26280010bc452dee3ca1f4e14bd6d480))
* **cad-record:** optional sRGB color on part intent, staling only the 3MF export ([4a8916f](https://github.com/benchhand/benchhand/commit/4a8916f81f2090cd4ca5087036f324bfe028cb15))
* **cad-record:** print each service class's rules at class choice ([#358](https://github.com/benchhand/benchhand/issues/358)) ([eb4c2d3](https://github.com/benchhand/benchhand/commit/eb4c2d3ab8555bed0de06ea91d88c67f27562b11))
* **cad-record:** record a printer's calibration state ([#354](https://github.com/benchhand/benchhand/issues/354)) ([d56d28b](https://github.com/benchhand/benchhand/commit/d56d28b928ff9b50fd1287ba85a5839340b362a7))
* **cad-record:** repair a stale release package of the same frozen manifest ([#257](https://github.com/benchhand/benchhand/issues/257)) ([c62249a](https://github.com/benchhand/benchhand/commit/c62249ad4b6fefc2c8e70329cfd65a183dc5cc0b))
* **cad-record:** report plausibility warnings from validate.py, with updated printer bands ([#299](https://github.com/benchhand/benchhand/issues/299)) ([9d17fc8](https://github.com/benchhand/benchhand/commit/9d17fc8d0a4c7363bcd4b8276cf67f46b2857c58))
* **cad-record:** require a unit-vector direction on FEA load cases ([b9a5ef0](https://github.com/benchhand/benchhand/commit/b9a5ef0306b460d54f23f212f6938425b5512f89))
* **cad-record:** shared finding schema ([a4071a1](https://github.com/benchhand/benchhand/commit/a4071a1c52574dc454e4182dd5e989323dc47ae8))
* **cad-record:** ship the FDM manufacturing guideline files ([#229](https://github.com/benchhand/benchhand/issues/229)) ([0f4770f](https://github.com/benchhand/benchhand/commit/0f4770f10735d07fd7086e1d5ad82d7be77a58e2))
* **cad-record:** strict &lt; and &gt; criterion operators and a shared tightens helper ([#244](https://github.com/benchhand/benchhand/issues/244)) ([765a337](https://github.com/benchhand/benchhand/commit/765a33744d4e746cd0e66a4a5b177b11b0e91cd8))
* **cad-record:** sub-builds and hold steps in assembly operations ([#334](https://github.com/benchhand/benchhand/issues/334)) ([95999a3](https://github.com/benchhand/benchhand/commit/95999a3255018cacaba62d2969410593d8a7a2c5))
* **cad-record:** warn on values outside their plausibility band ([#280](https://github.com/benchhand/benchhand/issues/280)) ([6bfab84](https://github.com/benchhand/benchhand/commit/6bfab8451fa34967eaae55bea76ad6e84019b61d))
* **cad-render:** hide or ghost chosen parts in renders and motion ([#332](https://github.com/benchhand/benchhand/issues/332)) ([90c08bb](https://github.com/benchhand/benchhand/commit/90c08bb047d5df2ead2c98d54fabe163fcb40872))
* **guidelines:** a nozzle set meeting a range keeps the sizes the range admits ([#275](https://github.com/benchhand/benchhand/issues/275)) ([ddd5ff5](https://github.com/benchhand/benchhand/commit/ddd5ff5431fe1ced467728ac1792359f35cf6cb1))
* **guidelines:** state the H2D sample's supported nozzle diameters ([#268](https://github.com/benchhand/benchhand/issues/268)) ([d67249f](https://github.com/benchhand/benchhand/commit/d67249f6b507dfbe641f1fb1254aa67eaf86f545))
* **harness:** agent model tiers, cheap-tier guard, cad-clerk ([7664830](https://github.com/benchhand/benchhand/commit/766483052a190c32252612fa52057ea8cc52615c))
* **lamp-arm:** add the Bambu Lab H2D capability profile for the printed parts ([#198](https://github.com/benchhand/benchhand/issues/198)) ([ee93c54](https://github.com/benchhand/benchhand/commit/ee93c54615237d6cb564ac35e87357f62351df7b))
* **output:** export findings as SARIF 2.1.0 ([e829737](https://github.com/benchhand/benchhand/commit/e8297372059ab4f301a5b57ff530fb4b7461751d))
* **quality:** complexity ratchet ([7743228](https://github.com/benchhand/benchhand/commit/7743228f9fc8af22cae2d6b38a1cba029a7c1e40))
* **schemas:** generate typed API and schema reference from the JSON Schemas ([#101](https://github.com/benchhand/benchhand/issues/101)) ([932b70b](https://github.com/benchhand/benchhand/commit/932b70b182ec9152fd0f4b9a569e0cffc105f66a))
* **skills:** hide helper cad-* skills from the / menu ([#322](https://github.com/benchhand/benchhand/issues/322)) ([c10f03f](https://github.com/benchhand/benchhand/commit/c10f03f7c580af1fdaec5e2625e7de8e23eed518))
* treat a not_assessed result as non-blocking and never as a pass ([#317](https://github.com/benchhand/benchhand/issues/317)) ([1cce4c7](https://github.com/benchhand/benchhand/commit/1cce4c725b2c77525dcf0de97344fdda791a57af))


### Fixes

* **agents:** keep the read-tool rule in the part designer prompt ([b775cdb](https://github.com/benchhand/benchhand/commit/b775cdb1e51f327599e918147fd9c2126e71b401))
* **agents:** stop preloading cad-drawings into the part designer ([65ed8c0](https://github.com/benchhand/benchhand/commit/65ed8c0896d4b9123ae55f85714da9e7016d85bd))
* **analysis:** demand proof for FEA convergence and service-load coverage ([b5f958e](https://github.com/benchhand/benchhand/commit/b5f958e9d4b2fbb54defa31269352734d9063af7))
* **cad-analysis:** a prismatic stop reacts the axial load ([#355](https://github.com/benchhand/benchhand/issues/355)) ([539f0a2](https://github.com/benchhand/benchhand/commit/539f0a27da041a67416f544d1d34630cbea97308))
* **cad-analysis:** check the record documents FEA reads against cad-record ([#262](https://github.com/benchhand/benchhand/issues/262)) ([fec5b4a](https://github.com/benchhand/benchhand/commit/fec5b4a2c40517817a911ed9e23ef216d2bff621))
* **cad-analysis:** compare_levels gives UNKNOWN for mesh sizes and coordinates the grid cannot index ([#235](https://github.com/benchhand/benchhand/issues/235)) ([c81ee53](https://github.com/benchhand/benchhand/commit/c81ee53d7881811afd7e8391205d365c2cf6184e)), closes [#234](https://github.com/benchhand/benchhand/issues/234)
* **cad-analysis:** FEA part selection, in-FreeCAD solver lookup, ccx threads, job isolation ([25fcdd3](https://github.com/benchhand/benchhand/commit/25fcdd3c83b029b56d2cbf120cc51cf47b6715d0))
* **cad-analysis:** raise FrdError for every malformed frd record ([#232](https://github.com/benchhand/benchhand/issues/232)) ([705fc47](https://github.com/benchhand/benchhand/commit/705fc47b2b866db32ad69c6418f6c4b52330f79d)), closes [#227](https://github.com/benchhand/benchhand/issues/227)
* **cad-analysis:** raise the FEA fixture script's Python floor to 3.12 ([f7caee8](https://github.com/benchhand/benchhand/commit/f7caee87ad509ac20f20a43185e39fc259ab1026))
* **cad-analysis:** read FEA load direction only as the record's unit vector ([8e0cc1c](https://github.com/benchhand/benchhand/commit/8e0cc1caf51039b14deed38801810ade3eccc487))
* **cad-analysis:** refuse a node id repeated inside one .frd block ([#238](https://github.com/benchhand/benchhand/issues/238)) ([a5dfceb](https://github.com/benchhand/benchhand/commit/a5dfcebd08f116c27ca4f85f0d5d2ee41e4f4375))
* **cad-analysis:** refuse an FEA run whose load reaches no mesh node or whose result is zero under load ([3b3dbf2](https://github.com/benchhand/benchhand/commit/3b3dbf25597f9d574d93baec15e6d36f8bf261f4))
* **cad-analysis:** state a load's direction in the analysed part's frame ([#243](https://github.com/benchhand/benchhand/issues/243)) ([4022e9f](https://github.com/benchhand/benchhand/commit/4022e9f231fd0b50dca70b6c7c40963fa6e3429f))
* **cad-analysis:** stiffness-only FEA needs no yield strength ([#357](https://github.com/benchhand/benchhand/issues/357)) ([ba0d45f](https://github.com/benchhand/benchhand/commit/ba0d45fbf147e776ead72f1ad6ab1ddb2ee25fb5))
* **cad-bom:** read datasheets as text and work without web search ([#362](https://github.com/benchhand/benchhand/issues/362)) ([135c492](https://github.com/benchhand/benchhand/commit/135c4927e2d5eaba39a2d7956564fc9a2aba397d))
* **cad-concept:** viewer lifecycle and node visibility (c7q.5) ([#328](https://github.com/benchhand/benchhand/issues/328)) ([df21c84](https://github.com/benchhand/benchhand/commit/df21c849a8fb9947763c29e7b442183d45c71c1a))
* **cad-design:** a process check with no stated limit returns unknown, not pass ([#306](https://github.com/benchhand/benchhand/issues/306)) ([f3ac58d](https://github.com/benchhand/benchhand/commit/f3ac58d7ef043976ec30b9018fd59f707d6370eb))
* **cad-design:** fail route.operation_plan when a plan field is missing ([#183](https://github.com/benchhand/benchhand/issues/183)) ([cfbdcea](https://github.com/benchhand/benchhand/commit/cfbdcea9827fac4f0a260a95cd58b6a826256b60))
* **cad-design:** guideline values govern when no calibration exists ([#335](https://github.com/benchhand/benchhand/issues/335)) ([dff84b5](https://github.com/benchhand/benchhand/commit/dff84b56882cf959b95be2be11dd3239fd0b0c8b))
* **cad-design:** identify purchased parts by standard designation or MPN ([d4bc852](https://github.com/benchhand/benchhand/commit/d4bc852ac5ee9335052f1306d36f7df50dcf48f9))
* **cad-design:** joint check reads bore or shaft from geometry; flat mounts by face contact ([#341](https://github.com/benchhand/benchhand/issues/341)) ([5dcd2c9](https://github.com/benchhand/benchhand/commit/5dcd2c929e69556c99bd07522fe75767ab5299c6))
* **cad-design:** judge print-bed stability on the contact hull and a calibrated minimum contact area ([42a0632](https://github.com/benchhand/benchhand/commit/42a06323bf378c16acb5f987593ffba1ae77a23a))
* **cad-design:** make the five failing overhang corpus twins self-supporting ([#199](https://github.com/benchhand/benchhand/issues/199)) ([f8a5cba](https://github.com/benchhand/benchhand/commit/f8a5cba82b0e9168eb61e08c06dcb93ad51a8fd2))
* **cad-design:** measure FDM bridge span across the roof ([9e2379a](https://github.com/benchhand/benchhand/commit/9e2379a9a69fb671c0254c2263da1e2207f61866))
* **cad-design:** pass the shipped guideline catalog in the mutation-corpus harness ([#313](https://github.com/benchhand/benchhand/issues/313)) ([2c9f9de](https://github.com/benchhand/benchhand/commit/2c9f9dea87ab8958fb5e04564907d77f7e3ddbff))
* **cad-design:** read the wall limit through the process checks' guideline scope ([#287](https://github.com/benchhand/benchhand/issues/287)) ([c8bfc5e](https://github.com/benchhand/benchhand/commit/c8bfc5e045853fa864be0ba1fd5cd07d5eae9c9c))
* **cad-design:** remove a stray paste of the cad-fits skill and guard skill sources ([0268cb9](https://github.com/benchhand/benchhand/commit/0268cb90ba3c9bb4a3a74ce71b47dc28498ec22c))
* **cad-design:** resolve occurrence references in purchase.mounting ([#129](https://github.com/benchhand/benchhand/issues/129)) ([707727f](https://github.com/benchhand/benchhand/commit/707727fd03ad2d57bad6635dc1db70a39a7d558f))
* **cad-drawings:** findings, not crashes, for stale locators and V-cut engravings ([#342](https://github.com/benchhand/benchhand/issues/342)) ([4f67071](https://github.com/benchhand/benchhand/commit/4f67071ce4d2be3b27ab390ef0fe4b643a0f9530))
* **cad-drawings:** lock the cad-fits load for worker threads ([#303](https://github.com/benchhand/benchhand/issues/303)) ([f7fb2ae](https://github.com/benchhand/benchhand/commit/f7fb2aebe9344945531e17be51c6c9a1d6ee2235))
* **cad-drawings:** one chamfer leg order; diameter fit on partial bores; no ±0 seats ([#338](https://github.com/benchhand/benchhand/issues/338)) ([722e088](https://github.com/benchhand/benchhand/commit/722e0889662b274c4cc39ba12bba7bc54f778681))
* **cad-fits:** give each unknown clearance reason its own remedy ([#296](https://github.com/benchhand/benchhand/issues/296)) ([886d8a2](https://github.com/benchhand/benchhand/commit/886d8a283f93f41d3e98f34622c5446390ee10a9))
* **cad-flow:** ask, record and validate the same start questions ([#345](https://github.com/benchhand/benchhand/issues/345)) ([7d58437](https://github.com/benchhand/benchhand/commit/7d58437591ff099b465db9add326d027f0371736))
* **cad-flow:** no machine profile means guideline values; accept user-declared identity ([#324](https://github.com/benchhand/benchhand/issues/324)) ([a924df5](https://github.com/benchhand/benchhand/commit/a924df5e005b0ca6191a2ba08dd51c746faeb211))
* **cad-flow:** scratch deliver reports a regenerated BOM; sync brings in retirements and other units' deliveries ([#353](https://github.com/benchhand/benchhand/issues/353)) ([64e144e](https://github.com/benchhand/benchhand/commit/64e144e6ce5a3631fbb10c4520199d64ed7cf8e4))
* **cad-flow:** stamps bind to the transition, not the date; flow-style YAML accepted ([#344](https://github.com/benchhand/benchhand/issues/344)) ([2ec59a9](https://github.com/benchhand/benchhand/commit/2ec59a9e5396ed65c9ccce3bc46bd7a0df303b98))
* **cad-flow:** terminate supervised groups without os.waitid on macOS ([552dc73](https://github.com/benchhand/benchhand/commit/552dc73b9e8068046d9b17cdc8e2b455c583464a))
* **cad-flow:** the FEA solver probe waits until FEA is enabled ([#350](https://github.com/benchhand/benchhand/issues/350)) ([b2aeb31](https://github.com/benchhand/benchhand/commit/b2aeb31dcc808488eb2f8159eb726b0abcae5b5b))
* **cad-model:** bind the 3MF color check to PartIntent.color ([#184](https://github.com/benchhand/benchhand/issues/184)) ([50390a0](https://github.com/benchhand/benchhand/commit/50390a0b467095b43e826f1b452a1c51ae4c870c))
* **cad-model:** clip lookup cell indices before the int64 conversion ([#293](https://github.com/benchhand/benchhand/issues/293)) ([8be8253](https://github.com/benchhand/benchhand/commit/8be82535d2b2521d0fece248baa02086e50006a4))
* **cad-model:** drop volatile fields from the hashed version manifest ([145165d](https://github.com/benchhand/benchhand/commit/145165d3f957d59e261d575f5bfc29ae75b0f3ec))
* **cad-model:** key manufacturing outputs by skills-relative code, not absolute paths ([#177](https://github.com/benchhand/benchhand/issues/177)) ([c8d5597](https://github.com/benchhand/benchhand/commit/c8d5597aa1caa15873ac67e6d41067fe1f8cd4a4))
* **cad-model:** list jsonschema in the cad_model.py script dependencies ([c75ac9c](https://github.com/benchhand/benchhand/commit/c75ac9cb6bcbfe2f81de323c2d72394990680905))
* **cad-model:** reject basematerials ids that repeat or collide with an object id in the 3MF reader ([#276](https://github.com/benchhand/benchhand/issues/276)) ([f8b90ed](https://github.com/benchhand/benchhand/commit/f8b90edd4bba717c9ab2af224f6518cb969bd0be)), closes [#123](https://github.com/benchhand/benchhand/issues/123)
* **cad-model:** write the HOLD readiness sidecar atomically ([bc8d3f9](https://github.com/benchhand/benchhand/commit/bc8d3f94894ee4f165db5ffd6869ce74e449e069))
* **cad-motion:** hand work and common tools need no modelled envelope ([#336](https://github.com/benchhand/benchhand/issues/336)) ([2749608](https://github.com/benchhand/benchhand/commit/274960807310bd324ec9c280889d22d0acd8e56d))
* **cad-motion:** interference fuzzy value above placement round-off ([#360](https://github.com/benchhand/benchhand/issues/360)) ([97e7e00](https://github.com/benchhand/benchhand/commit/97e7e009784d84adfa2cd5056ef06a91bb1980d7))
* **cad-motion:** put each producer's own code in the recipe that reuse matches ([a7d007e](https://github.com/benchhand/benchhand/commit/a7d007e3ae9f12ba4bb2113f79584885450beed8))
* **cad-motion:** read a FreeCAD job's result again after it exits, and write job result files whole ([b110206](https://github.com/benchhand/benchhand/commit/b1102063eb73c91133c21771dd07afc8ad009861))
* **cad-motion:** sweeps use their declared endpoints exactly ([#351](https://github.com/benchhand/benchhand/issues/351)) ([6cdc291](https://github.com/benchhand/benchhand/commit/6cdc291e38bc8205109ee1399e11ffb4306974de))
* **cad-recheck:** approve a stage advance only on its gate decision ([#318](https://github.com/benchhand/benchhand/issues/318)) ([6575b88](https://github.com/benchhand/benchhand/commit/6575b883bdd160ea418b88dcd52aca086d9f7b08))
* **cad-recheck:** let stamp --verify accept a stamp at the gate just passed ([074280f](https://github.com/benchhand/benchhand/commit/074280f2d967bfb7299c9c1e17fb55724101b77c))
* **cad-record:** a more specific unresolvable guideline limit makes the result unknown ([#264](https://github.com/benchhand/benchhand/issues/264)) ([1190d5b](https://github.com/benchhand/benchhand/commit/1190d5b156d19603f66a59d0dcab2d501b2f08d2))
* **cad-record:** a wrong-typed record key is a finding, not a crash ([#321](https://github.com/benchhand/benchhand/issues/321)) ([dbc91f1](https://github.com/benchhand/benchhand/commit/dbc91f10639e47f13745604bedd40545d5950e7c))
* **cad-record:** an undeclared click dependency makes release.py --prune report incomplete ([#228](https://github.com/benchhand/benchhand/issues/228)) ([6a2861f](https://github.com/benchhand/benchhand/commit/6a2861fe95d404e7e2b47b9fd701d73bc2bb6e22)), closes [#181](https://github.com/benchhand/benchhand/issues/181)
* **cad-record:** declare manufacturing_intent script dependencies ([#281](https://github.com/benchhand/benchhand/issues/281)) ([49abed4](https://github.com/benchhand/benchhand/commit/49abed4aea9ed18b454acb8f1419e0c8a405d9fb))
* **cad-record:** dispute equal-rank limits only across distinct defining guidelines ([#295](https://github.com/benchhand/benchhand/issues/295)) ([bdefa33](https://github.com/benchhand/benchhand/commit/bdefa33c8060ebf8274f554817c72c5d762c1402))
* **cad-record:** fail an assembly occurrence no operation installs or names ([88be42e](https://github.com/benchhand/benchhand/commit/88be42e5e8270f4441e8c1a53a17eb5c94c9b900))
* **cad-record:** findings, not crashes, for missing evidence files and blank commodity standards ([#343](https://github.com/benchhand/benchhand/issues/343)) ([dbca930](https://github.com/benchhand/benchhand/commit/dbca930e9a1553077866b0a414c0035a6167b5ba))
* **cad-record:** hash what place_object() renamed in, under a folder lock ([#253](https://github.com/benchhand/benchhand/issues/253)) ([0ed6094](https://github.com/benchhand/benchhand/commit/0ed6094151aac636a83995da109cff9bd0f910c6))
* **cad-record:** hash what the store publishes and trust a cached hash only once settled ([#247](https://github.com/benchhand/benchhand/issues/247)) ([672ca39](https://github.com/benchhand/benchhand/commit/672ca393e6adf340d57be907ef27a210b68d032b)), closes [#245](https://github.com/benchhand/benchhand/issues/245)
* **cad-record:** keep plausibility checks from overflowing on huge integers ([#308](https://github.com/benchhand/benchhand/issues/308)) ([dd34b3e](https://github.com/benchhand/benchhand/commit/dd34b3e5caf946b85c0bcee529ce46ae0c052bea))
* **cad-record:** no false passes in selection and obligation identity ([#346](https://github.com/benchhand/benchhand/issues/346)) ([a3cb5ec](https://github.com/benchhand/benchhand/commit/a3cb5ec1ece406d3677a9ace0b30af6db73bfe91))
* **cad-record:** normalize every decision-record front-matter failure to DecisionRecordError ([#225](https://github.com/benchhand/benchhand/issues/225)) ([aea4e02](https://github.com/benchhand/benchhand/commit/aea4e02c154b031edc4edb8235281059d0ce5a84))
* **cad-record:** point schema problems at the failing key inside a oneOf/anyOf ([#284](https://github.com/benchhand/benchhand/issues/284)) ([bfb46ae](https://github.com/benchhand/benchhand/commit/bfb46ae15b4fc9cf66090c8fbe9075e69dedb72e))
* **cad-record:** refuse a cached hash after a clock step, and re-hash what place() renamed in ([#249](https://github.com/benchhand/benchhand/issues/249)) ([cb3bdab](https://github.com/benchhand/benchhand/commit/cb3bdabf4362665cbd2d32abec78723256b8ffb1))
* **cad-record:** refuse evidence that cites scratch or unlisted paths, and evidence that relies on it ([6f97fd4](https://github.com/benchhand/benchhand/commit/6f97fd410a1dacf3c24f45cfe1e4d76f655ab327)), closes [#152](https://github.com/benchhand/benchhand/issues/152)
* **cad-record:** report an edited shipped guideline copy as stale ([#267](https://github.com/benchhand/benchhand/issues/267)) ([c8ba036](https://github.com/benchhand/benchhand/commit/c8ba0361a64a8f3ed05e256efb121755a7fb273c))
* **cad-record:** reread a schema whose file times fall in the tick it was read ([d159b30](https://github.com/benchhand/benchhand/commit/d159b303c0ecf7367ded617d52b4229f7a814bbf))
* **cad-record:** serialize fixture module loading across threads ([#282](https://github.com/benchhand/benchhand/issues/282)) ([eefec43](https://github.com/benchhand/benchhand/commit/eefec43bf95d9a1289680df52d6bdb7dcc46e5f1))
* **cad:** pin numpy and guard the Python range across cad-model and cad-motion script headers ([cbcb52e](https://github.com/benchhand/benchhand/commit/cbcb52e4f2d456b3bde74b108496f1f530214388))
* **ci:** reconcile [#56](https://github.com/benchhand/benchhand/issues/56) and [#57](https://github.com/benchhand/benchhand/issues/57) with the docs-only checks ([cff7a78](https://github.com/benchhand/benchhand/commit/cff7a78bb75f48a1cd15f99e334575e6de4d310a))
* **drawings:** make exported PDFs byte-reproducible ([7b70cfe](https://github.com/benchhand/benchhand/commit/7b70cfe79fa2938ec5001d767f4febb88c71c89a))
* **examples:** correct LMP-0010's insert citation to an M10x1 part ([7fe8022](https://github.com/benchhand/benchhand/commit/7fe8022931e8621c7aa3d8b167c86e460a471173))
* **examples:** record manufacturing routes for every lamp-arm part ([620011c](https://github.com/benchhand/benchhand/commit/620011cb5cfbaf6421305ed738aa0fe260af6947))
* **mfg:** a corpus case that errors fails the rates step ([#202](https://github.com/benchhand/benchhand/issues/202)) ([d14ad0a](https://github.com/benchhand/benchhand/commit/d14ad0ab050cffdf8f7d60b4a7c031f2e6fce08f))
* **mfg:** form_check_kernel_validity mutants build and cad_model check catches them ([#246](https://github.com/benchhand/benchhand/issues/246)) ([f64ad38](https://github.com/benchhand/benchhand/commit/f64ad38a38e2ee6bcb0f362d25b0d0dac9cd2801))
* **motion:** exempt a fitted partner over the final engagement only ([#53](https://github.com/benchhand/benchhand/issues/53)) ([4da589b](https://github.com/benchhand/benchhand/commit/4da589bd113c658c850635b6b0cdb494f4070c31))
* **motion:** make the STEP FILE_NAME time deterministic ([5fcbb02](https://github.com/benchhand/benchhand/commit/5fcbb027f9c7f95b6a677ccff607e33ed5c63a4f))
* no false passes in heat-set bores, resin flatness, ghosted visibility, functional fillets and play ([#349](https://github.com/benchhand/benchhand/issues/349)) ([f7f591c](https://github.com/benchhand/benchhand/commit/f7f591ccd0bb89b2dfa882ac7b5fd2055d14546b))
* open defects (shadowed tests, build_guide version gate, docs, dead gates) ([31c7ee7](https://github.com/benchhand/benchhand/commit/31c7ee730b481bea39523cb983c77746667f4db8))
* **record:** narrow guideline staleness to a part's own materials ([#274](https://github.com/benchhand/benchhand/issues/274)) ([7a9d053](https://github.com/benchhand/benchhand/commit/7a9d053b3b0f49c24d34c321c2f78e43b560ac25))
* **schemas:** type open objects without declared properties as Mapping ([#292](https://github.com/benchhand/benchhand/issues/292)) ([a050f3f](https://github.com/benchhand/benchhand/commit/a050f3fae873ac1f037acc4a3e4e68de6e2735af))
* **test:** don't depend on whether lamp-arm validates in the --json flag test ([7b815b6](https://github.com/benchhand/benchhand/commit/7b815b6cfa19da5fa585261171fae93a6c031c01))
* **types:** clear the 107 mypy errors in the scoped type check ([9e958bf](https://github.com/benchhand/benchhand/commit/9e958bf5879306e3d3d2372488bc89c83469dcfe))


### Performance

* bounded native job defaults, parallel sheet export, safer job cancellation ([2f0b457](https://github.com/benchhand/benchhand/commit/2f0b457f031e87bbe7c056de2e4e6146379e0db5))
* **cad-model:** build a kernel's parts in parallel forkserver workers, one fresh worker per part ([a419293](https://github.com/benchhand/benchhand/commit/a419293523aec923dff30bdccc967f8691229007))
* **cad-model:** key the agreement sample grid by one integer cell key ([#301](https://github.com/benchhand/benchhand/issues/301)) ([e7fdaaf](https://github.com/benchhand/benchhand/commit/e7fdaaf0467d96180015a2e084f1dacd6205df28))
* **cad-model:** look up agreement triangles in a fine grid ([#286](https://github.com/benchhand/benchhand/issues/286)) ([ab2adc5](https://github.com/benchhand/benchhand/commit/ab2adc5162d1a45a29a70bdbb9e6defdeae6db8b))
* **cad-model:** measure each read-back part shape once per manufacturing check ([a69bbb4](https://github.com/benchhand/benchhand/commit/a69bbb4667ad1e498909cb6a7cc2e5a9db3f28db))
* faster record validation, YAML parsing and release tests ([f9e6aa7](https://github.com/benchhand/benchhand/commit/f9e6aa769d5505f4c6c0f4a976361d5b1293279c))
* **output:** compact agent stdout with one emitter and a tool-result envelope ([d3695b9](https://github.com/benchhand/benchhand/commit/d3695b9b48c589920c9cbdca5a8a0a7f8fb102b3))
* **test:** run the shared V6 sweep once in test_motion ([33743a0](https://github.com/benchhand/benchhand/commit/33743a01ce41579e5bcb9f9a8d25a619bc0fbe0f))


### Documentation

* add AGENTS.md and the architecture doc ([#283](https://github.com/benchhand/benchhand/issues/283)) ([77732c8](https://github.com/benchhand/benchhand/commit/77732c8d171ca2aa0e93e8d1fac62007b71d2b91))
* add the engineering doc ([#279](https://github.com/benchhand/benchhand/issues/279)) ([42a3590](https://github.com/benchhand/benchhand/commit/42a359053511a5e45093db7f75d4748436338e56))
* **cad-analysis:** record the near-support thresholds as engineering judgement ([#285](https://github.com/benchhand/benchhand/issues/285)) ([9457218](https://github.com/benchhand/benchhand/commit/9457218381524c9b42ad0f504bc743a79e4e0ec0))
* **cad-flow:** one project per variant ([#339](https://github.com/benchhand/benchhand/issues/339)) ([d4387f1](https://github.com/benchhand/benchhand/commit/d4387f1bdecfa9d315dbf8307ddf3b49e029ec6d))
* **cad-record:** define engagement_length_mm as bore depth travel ([#124](https://github.com/benchhand/benchhand/issues/124)) ([01b7b7b](https://github.com/benchhand/benchhand/commit/01b7b7b701b0d6af1f413e3b7acf851e8b76ce73))
* **cad-record:** procurement specifications live in the part's sources folder ([#359](https://github.com/benchhand/benchhand/issues/359)) ([ffe4c32](https://github.com/benchhand/benchhand/commit/ffe4c3249d815f71ce9ad94363f1b0a677be0010))
* **engineering:** document the schema API generator ([#302](https://github.com/benchhand/benchhand/issues/302)) ([e9f1d50](https://github.com/benchhand/benchhand/commit/e9f1d503fbe75d0bec907bff697c049c7dd278b9))
* **flow:** write the writer cap, merge rule, 600 s limit and stall handling as process ([5b938fb](https://github.com/benchhand/benchhand/commit/5b938fbd08ac67e2021d3af83df416b5866065ba))
* JSON Schema is the contract source; no TypeSpec ([#352](https://github.com/benchhand/benchhand/issues/352)) ([ed2ea9d](https://github.com/benchhand/benchhand/commit/ed2ea9da875e333a50eeb04c6c993d56222f6d86))
* quick start, reference, troubleshooting and docs index ([#326](https://github.com/benchhand/benchhand/issues/326)) ([7f174e3](https://github.com/benchhand/benchhand/commit/7f174e32ad53349d7d009249eafa9c7a41338d3b))


### Refactoring

* **cad-flow:** setup reads the git storage answer from flow.profile ([#312](https://github.com/benchhand/benchhand/issues/312)) ([4610ddd](https://github.com/benchhand/benchhand/commit/4610ddd4688681d7f6d9e3fc7822d8e6833c90b4))
* **cad-model:** move cad_model.py's CLI from argparse to Click ([e1ca631](https://github.com/benchhand/benchhand/commit/e1ca631ffeaa9c1ef75308f57a84f1863aad3314))
* **cad-record:** drop the unused rigor field from product.yaml ([#219](https://github.com/benchhand/benchhand/issues/219)) ([696b672](https://github.com/benchhand/benchhand/commit/696b672f757083713cec3854cc9b32a6b325710e))
* **cad-record:** drop the version field from check packets ([20a8786](https://github.com/benchhand/benchhand/commit/20a878640685f03f0af9931fba51cecb3a602427))
* **cad-record:** make schema_registry the single validator path ([752206f](https://github.com/benchhand/benchhand/commit/752206f6845aeb840b8066942047896d4aaad7d4))
* **cad-record:** one rules table for service-class scope ([#347](https://github.com/benchhand/benchhand/issues/347)) ([f3bb089](https://github.com/benchhand/benchhand/commit/f3bb089317e5a06021b6bac54f91a4fc20cc99ef))
* **cad-record:** remove record version 1 support ([#65](https://github.com/benchhand/benchhand/issues/65)) ([a41f295](https://github.com/benchhand/benchhand/commit/a41f29552a02d114e9f535d534da812a3254a2f2))
* **cad-record:** split migrate.plan into per-file planners ([c6e1662](https://github.com/benchhand/benchhand/commit/c6e166215d48d056b091d7cc24d7b18cb0a56ee5))
* **harness:** split install and uninstall into helpers ([dc818f1](https://github.com/benchhand/benchhand/commit/dc818f1692cfe341346fed7b678eeb4d7a183d5e))
* split long functions in sheet, assembly_check and drawing_document ([2df6147](https://github.com/benchhand/benchhand/commit/2df6147d8f5b03ac3058b95084350274ac5510d7))

## 0.1.1 (2026-11-02)

Fixes from the first live run in Claude Code, Codex CLI, OpenCode and Kiro CLI.

- The concept viewer's Save screenshot, Fit view and label toggles work again; Save did nothing because their click handlers were missing.
- Preflight loads its sibling-skill modules at startup, so changing the installed skills during a long first run gives a full report instead of a traceback. A failed check reports the exception line, so a cold run names the missing Chromium executable instead of Playwright's banner.
- cad-flow allows one active lead session per project and re-reads `hw/` before resuming. A cancelled or unanswered gate question is not consent: the gate stays open and the stage unchanged.
- `release.py` has no preview flag; a refused run writes nothing outside `.cad-work/` and is not a successful dry run.
- The README no longer calls the Claude Code marketplace private, and describes the missing optional KiroCrew prompt file as the non-fatal warning it is.

## 0.1.0 (2026-10-31)

First standalone release of the CAD skills, adapted for Claude Code, OpenAI Codex CLI, OpenCode, and Kiro CLI without OMP or Beads dependencies.

- Provide a stage-based workflow from concept and design records through checked STEP AP242 models, drawings, EBOM, verification, and release packages, with user-approved gates recorded as decisions.
- Add skills for CAD modeling, design fidelity, motion, rendering, interactive viewing, fits, analysis, BOM, drawings, record validation, and engineering critique.
- Include FreeCAD 1.1.4, CadQuery, build123d, OCP, and Chromium preflight and setup; generate harness-native agent profiles and install files for supported harnesses.
- Preserve immutable, manifest-bound versions and evidence bundles, manufacturable part outputs, and the lamp-arm reference project.
- Check each built version fail-fast, cheapest first, and close LAYOUT freeze, PARTS, DETAIL and ANALYSIS with two gates: a provisional decision the lead records once the fast checks pass, then the user's approval after the full check. A failed full check, a MAJOR L2 light-review finding or a change the user names voids the provisional decision and reopens that stage and every later stage built on that version. A gate decision's first line is `Status: APPROVED`, `Status: PROVISIONAL` or `Status: VOID`; `validate.py` and `release.py` refuse a PROVISIONAL or VOID decision as the freeze, change or release approval.
- Sweep with between-sample collision and clearance proofs, subsystem runs and merges, a shared pair cache in `hw/.cad/pairs-v2.sqlite`, changed-only static checks and smoke sweeps, and supervised run records under `hw/.cad/runs/` that refuse stray or concurrent runs.
- Catch missing torque paths and axial retainers, and see-through FDM parts, at L2: records at `parts` or later declare `torque` on each revolute and an `axial_retainer` on each press and locating fit.
- Build only changed parts and store each part's outputs once; keep generated results out of git unless `product.yaml` `storage.results_in_git` approves them, with `setup.py --storage` writing the git rules.
- Prune superseded versions after a release, and generate the OKH and DIN SPEC 3105-1 documents from the record.
- Check requirement window visibility in renders and reuse unchanged views; render custom view directions.
- Tolerate cosmetic fillet failures, smoke FreeCAD runs, diagnose CalculiX crashes and timeouts, and dimension hole centres from datum planes.
- Refuse to install the recheck hook over a `core.hooksPath` set in the system or global git config; verify with `stamp.py --verify` instead.
- Check each made part's minimum wall at F2 (FDM 1.2 mm unless `product.yaml` `manufacturing.min_wall_mm` overrides it), and apply `block_like` only to parts whose `form_class` is `solid`.
- Keep shared-environment launches working for scripts named `setup.py`, `pyproject.toml`, or `setup.cfg`; ignore generated output when delivering a scratch workspace; stop at gates when the lead cannot ask the user.

### Migration notes

- The pair cache moved to `hw/.cad/pairs-v2.sqlite`, and sweep `timings_s` counters are renamed.
- Records at `parts` or later need `torque` on every revolute and an `axial_retainer` on each press and locating fit (see `cad-record/references/design-rules.md`).
- A too-thin or unmeasured wall is a form-check finding, and an invalid `min_wall_mm` override exits 2.
- Gate decisions carry a `Status:` first line instead of Beads gates.
