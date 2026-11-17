# Apple container cannot start the stock redroid image: before the first process it writes /etc/hosts and /etc/resolv.conf into the
# root file system, and the stock /etc is an absolute symlink (-> /system/etc) that does not resolve on the host side
# (error "configureHosts"). This rebuild makes /etc a real directory; nothing else changes.
# Build: container build -t kelo-redroid:15 -f tools/build/android-kernel/redroid.Dockerfile tools/build/android-kernel
FROM redroid/redroid:15.0.0-latest AS src
FROM alpine:latest AS fix
COPY --from=src / /r/
RUN rm /r/etc && mkdir /r/etc
FROM scratch
COPY --from=fix /r/ /
WORKDIR /
ENTRYPOINT ["/init","qemu=1","androidboot.hardware=redroid"]
