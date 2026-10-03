# L3
# Input: 无运行时输入；内容为已核实的技能结构指纹常量。
# Output: 已核实结构签名的常量集合。
# Pos: Data / Sources 内 Haneoka 成员技能的结构指纹契约，供 haneoka_members 使用；见 ../L2-2.md。
# Effects/Dependencies: 只定义签名常量，无外部 I/O 或运行时指纹计算。

"""Verified JP skill-mechanics fingerprints, not numeric results.
Sources: Haneoka JP r-544e9e1285777b600e3f (2026-09-28),
r-d82e1e0581c690d38a27 (2026-10-04: cards 61/62/64).
Each SHA256 covers the Japanese template, Lv.5 effect mechanics and referenced
conditions. Effect magnitudes alone vary; unknown contracts stay unavailable.
"""
PROFILES = {'be104665194da04ef265f0a7d926aa91f956d272ad6b89f89d62b8767d9e1658': 'gauge_life_guard',
    '5c16d2efd49abf61e1279ad50425be8e962708d57777c5180ef54a0eeb0eab82': 'live_plain',
    '1ea2ce831ab39698593642dc99c137bb6edcfa662b3b5cc21cc9ec2f0b95b841': 'live_perfect',
    '106726848063cb7fa62a169373ac7373359d06b96b5fbe4edff7c941f73631af': 'live_life',
    '0def1a832a5633f5f0fb99cbb62b05308ec2f1c9c2e6ed72e244ac51757cc4e9': 'just',
    '4a3c56418acc121ddfb0cccacb66afa5106134627e3d5a22cd57b9b57c4344e4': 'stack',
    '48fb0dd6602b6da1b47e6ad7ee04c576945ad434d63952686ee1a47878905586': 'stack',
    '97c5c5911916d826696eabb3331052a03c79cfaf52d778167a3c17647fe8692b': 'combo_threshold',
    '2b1b0776611c54240608ee9c2142cca81d136acbdfe8884987b4c44dc9510e7f': 'points',
    '874d38302ad64166b6d8957cd89977dfa0a2c84594ff5ee7036634503ccb3c3b': 'just_life',
    '9a06cb0282d01bb08960f1768c2b8fe6d6eaade04c41f0a0febd2ee2583270de': 'combo_life',
    'cf2afa31ec221e1d94f27317bc5e16915b5022850585db1d8d20bbf1a72feb42': 'probability',
    '2f1caa84e30c89962bb0cee9d2d7ddd764332a499410f7b3534a1b1018b747a9': 'combo',
    '11e6704c89d34facf9b9995cce882954978eeed583659e3870e7bc2418fee3ca': 'gauge'}

# Leader contracts also include resolved target mechanics (band / LIVE category).
LEADER_PROFILES = {
    '0208e62056f9b10b77ab346b72b31ce16226603c80376d8df903ad90221df72f',
    '1f2d95e0a8a14e42238fb147374555d9379c2ef0414d146cc577df134e6592ac',
    '21115635759ff6a82b258e500fe24a9f0958dc1aee6acd5855d85eb5ef3d686b',
    '2ebd782dca09aa6368b59f6372af3cd04e767f5b55b256877a49372fcc4e788c',
    '32a49d027f4352cdbca178f595efa9735656b21454ccb47ba5874a0ab6281d1b',
    '55905dde70bc384a88ebf6cc02fe752dd48639f4a0492548093f3551603f0733',
    '55cb1c8a1c68ff88b1c20c3c106f83764e7760b3462d3451ffe9f489d8d81844',
    '5cdf82b8e8af925cf4b6fe3121f67ab20d4183c66aec40385c3fbbc1276bf2fa',
    '83df25d5767fb71c686625fb4f2cb3f91104ff3aaea8a69b387f45c9c53b30e6',
    'a713c023b0d31d3125930f258934b6bdc542010d5267a3f3de0e1e7cc5d57284',
    'b25407ad73b4db0c0d44e9e8b94a0610846dedfd3155bdcac109c7e05187b863',
    'bd9af3ea2a16dbec9eb49268f3bbf9002ab8bb2a506d6f5e46da3b6bd32b214a',
    'c1978e58931db87eb5c14cd37fc0899a9606cb271a1ba8ac82e2bc2828dc0702',
    'c2a33a5d5b3f86f53a20f8e6dc9a798264aaacb82dd73e2a213eb87f15fa8e02',
    'c61299c040cafade307d98665a67a20fb6784d344e6e045da27b40b14f2c88ab',
    'cde1227bfb9ae928dd7ffa06318dc4b7a268d16492b062ddf6139d72b9b7d1c3',
    'cf8289005512888cf6ff2365650d87fd8ae706e08bbc660f9923ce6a283a4a99',
    'd1d633c687be64c988a0c57ca0456d53576ca0ae0cbf30132e84078b958ac370',
    'd64edb1fc59cd33986d970d0a283c5abeb17b8af2044f1f60f3f5674867b696f',
    'd7488fdff741d0f2995f22e4dfa2d424dfe5ec3560d816d8f0ed14ba5de8de74',
    'e5e50249b8acd3cff55326904c63e073ec1881319665aec8f5669f2a33875631',
    'f0ce96a79ab8606770f85026a85b9847c26efc6ae257d998ec6bddcb5a9acab7',
    'f4a81a278cf687a4eaf2962c23dd0116a4c9137c2aca48045108b3ed3aedfc46',
    'feaa1ccff5bb4e2850ae916a80af21b84dad5fe1817db717f61db2663b41c920',
}

# Verified JP release r-d82e1e0581c690d38a27, 2026-10-04; card IDs 61/62/64.
# Ordered, independently targeted effects (not an unconditional combined bonus).
LEADER_COMBINATIONS = {
    "2f055492d033851b96895dbc3b0296e467d6563bf0727eea84fadb1b49524ab8": ("color", "band"),
    "cc7846a2bcfd8f6a02445c42ca5f262ef2add8fce59ae0dab00f9b697bfba93a": ("band", "gekisou"),
    "4d4cc313b7de262d777fd49c272d24b6d062c2986dc232e0489c05fc4e8323a2": ("band", "color"),
}
