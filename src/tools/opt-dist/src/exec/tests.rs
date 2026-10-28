use super::*;

#[test]
fn llvm_profile_cache_input() {
    let build =
        || Bootstrap { cmd: cmd(&["python", "x.py", "build"]), metrics_path: Utf8PathBuf::new() };
    let inherited = std::env::var_os("SCCACHE_EXTRAFILES");
    let profile = LlvmPGOProfile(Utf8PathBuf::from("profile dir;cache/llvm.profdata"));
    let optimized = build().llvm_pgo_optimize(Some(&profile)).unwrap();

    assert_eq!(
        &optimized.cmd.args[3..],
        ["--set", &format!(r#"pgo.llvm.use="{}""#, normalize_path(&profile.0))]
    );
    if cfg!(windows) {
        let mut expected =
            inherited.as_ref().map(std::env::split_paths).into_iter().flatten().collect::<Vec<_>>();
        expected.push(profile.0.clone().into_std_path_buf());
        assert_eq!(
            optimized
                .cmd
                .env
                .get("SCCACHE_EXTRAFILES")
                .map(|value| std::env::split_paths(value).collect::<Vec<_>>()),
            Some(expected)
        );
    } else {
        assert!(!optimized.cmd.env.contains_key("SCCACHE_EXTRAFILES"));
    }

    assert!(!build().llvm_pgo_optimize(None).unwrap().cmd.env.contains_key("SCCACHE_EXTRAFILES"));
    assert!(
        !build()
            .llvm_pgo_instrument(Utf8Path::new("profiles"))
            .cmd
            .env
            .contains_key("SCCACHE_EXTRAFILES")
    );
    assert_eq!(std::env::var_os("SCCACHE_EXTRAFILES"), inherited);
}
