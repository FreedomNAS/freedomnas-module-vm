# Maintainer: FreedomNAS Team <dev@freedomnas.io>
pkgbase=freedomnas-module-vm
pkgname=(freedomnas-module-vm freedomnas-module-vm-openrc freedomnas-module-vm-dinit freedomnas-module-vm-runit freedomnas-module-vm-s6)
pkgver=0.1.0
pkgrel=1
pkgdesc="Virtual machines for FreedomNAS: libvirt on Linux, vm-bhyve on FreeBSD"
arch=('any')
url="https://codeberg.org/FreedomNAS/freedomnas-module-vm"
license=('MPL-2.0')
depends=('freedomnas-core>0.9.12' 'freedomnas-webui>0.9.12' 'qemu-base' 'libvirt' 'edk2-ovmf' 'dnsmasq')
source=("git+https://codeberg.org/FreedomNAS/freedomnas-module-vm.git#tag=v${pkgver}")
sha256sums=('SKIP')

_install() {
    cd "${srcdir}/freedomnas-module-vm"

    mkdir -p "${pkgdir}/usr/lib/freedomnas/modules"
    mkdir -p "${pkgdir}/usr/lib/freedomnas/profiles"
    cp -r modules/vm  "${pkgdir}/usr/lib/freedomnas/modules/vm"
    cp -r profiles/vm "${pkgdir}/usr/lib/freedomnas/profiles/vm"

    if [ -f LICENSE ]; then install -Dm 644 LICENSE "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"; fi
    if [ -f README.md ]; then install -Dm 644 README.md "${pkgdir}/usr/share/doc/${pkgname}/README.md"; fi
}

package_freedomnas-module-vm() {
    _install
}

package_freedomnas-module-vm-openrc() {
    pkgdesc+=" (openrc services, Artix)"
    depends+=('libvirt-openrc')
    provides=("freedomnas-module-vm=${pkgver}")
    conflicts=(freedomnas-module-vm)
    _install
}

package_freedomnas-module-vm-dinit() {
    pkgdesc+=" (dinit services, Artix)"
    depends+=('libvirt-dinit')
    provides=("freedomnas-module-vm=${pkgver}")
    conflicts=(freedomnas-module-vm)
    _install
}

package_freedomnas-module-vm-runit() {
    pkgdesc+=" (runit services, Artix)"
    depends+=('libvirt-runit')
    provides=("freedomnas-module-vm=${pkgver}")
    conflicts=(freedomnas-module-vm)
    _install
}

package_freedomnas-module-vm-s6() {
    pkgdesc+=" (s6 services, Artix)"
    depends+=('libvirt-s6')
    provides=("freedomnas-module-vm=${pkgver}")
    conflicts=(freedomnas-module-vm)
    _install
}
