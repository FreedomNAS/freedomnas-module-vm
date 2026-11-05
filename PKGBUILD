# Maintainer: FreedomNAS Team <dev@freedomnas.io>
pkgname=freedomnas-module-vm
pkgver=0.1.0
pkgrel=1
pkgdesc="Virtual machines for FreedomNAS: libvirt on Linux, vm-bhyve on FreeBSD"
arch=('any')
url="https://codeberg.org/FreedomNAS/freedomnas-module-vm"
license=('MPL-2.0')
depends=('freedomnas-core>0.9.12' 'freedomnas-webui>0.9.12')
optdepends=("libvirt: the hypervisor's manager, installed from the WebUI otherwise"
            "qemu-base: runs the machines, installed from the WebUI otherwise")
source=("git+https://codeberg.org/FreedomNAS/freedomnas-module-vm.git#tag=v${pkgver}")
sha256sums=('SKIP')

package() {
    cd "${srcdir}/freedomnas-module-vm"

    mkdir -p "${pkgdir}/usr/lib/freedomnas/modules"
    mkdir -p "${pkgdir}/usr/lib/freedomnas/profiles"
    cp -r modules/vm  "${pkgdir}/usr/lib/freedomnas/modules/vm"
    cp -r profiles/vm "${pkgdir}/usr/lib/freedomnas/profiles/vm"

    if [ -f LICENSE ]; then install -Dm 644 LICENSE "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"; fi
    if [ -f README.md ]; then install -Dm 644 README.md "${pkgdir}/usr/share/doc/${pkgname}/README.md"; fi
}
