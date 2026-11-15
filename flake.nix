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
            };
          });
    };
}
