{
  description = "Dolos development tools";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  outputs = { nixpkgs, ... }:
    {
      devShells = nixpkgs.lib.genAttrs
        [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ]
        (system:
          let pkgs = import nixpkgs { inherit system; };
          in {
            default = pkgs.mkShell {
              packages = [ pkgs.nodejs_26 pkgs.pnpm pkgs.uv ];
              # The macOS XGBoost wheel needs OpenMP; Linux CPU wheels bundle it.
              shellHook = pkgs.lib.optionalString pkgs.stdenv.hostPlatform.isDarwin ''
                export DYLD_LIBRARY_PATH="${pkgs.llvmPackages.openmp}/lib''${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
              '';
            };
          });
    };
}
