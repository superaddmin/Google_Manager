#!/usr/bin/env python3
"""Build an inspectable, fail-closed deployment preparation bundle.

This command performs no network, Docker, deployment, or secret operations.  It
only validates existing release evidence, copies an explicit public allowlist,
and records hashes for independent review.

The Git executable, PATH, CI runner, and supplied CI URL are trust boundaries.
Run this tool only on a controlled release runner and independently verify
artifact provenance; the command does not authenticate the URL's contents.
"""

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid
import zlib
from urllib.parse import parse_qs, unquote, urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_EVIDENCE_BYTES = 64 * 1024 * 1024
MAX_PUBLIC_FILE_BYTES = 16 * 1024 * 1024
MAX_SBOM_COMPONENTS = 10_000
MAX_SBOM_DEPTH = 32
GIT_TIMEOUT_SECONDS = 15
IMAGE_PATTERN = re.compile(
    r"^(?P<repository>[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?::[0-9]+)?/[a-z0-9._/-]+)@sha256:(?P<digest>[0-9a-f]{64})$"
)
SAFE_OUTPUT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
KNOWN_SEVERITIES = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
SUPPORTED_PLATFORMS = ("linux/amd64", "linux/arm64")
PLACEHOLDER_MARKERS = (
    "change_me", "changeme", "replace_me", "replace-me", "example.invalid",
    "your-domain", "your_domain", "<replace", "<change",
)

PUBLIC_FILES = (
    "docker-compose.yml",
    "deploy/prepare-compose-host.sh",
    "deploy/chromium-seccomp.json",
    "deploy/online_smoke.py",
    "deploy/systemd/google-manager-compose-monitor@.service",
    "deploy/systemd/google-manager-compose-monitor@.timer",
    "deploy/env.production.example",
    "deploy/preflight.py",
    "deploy/backup_database.py",
    "deploy/compose_release.py",
    "deploy/browser-artifacts.json",
    "deploy/nginx/google-manager.conf",
    "docs/deployment-technical-guide.md",
    "docs/deployment-preparation.md",
    "docs/production-manual-configuration.md",
    "docs/cdk-implementation-and-operations-2026-09-25.md",
    "docs/browser-security-baseline-2026-09-20.md",
    "docs/server-deployment-guide.md",
    "docs/release-signoff-template.md",
    "LICENSE",
)

LOCKFILES = (
    "requirements.txt",
    "package-lock.json",
    "frontend/package-lock.json",
    "googlemail/package-lock.json",
)

CYCLONEDX_VERSIONS = {"1.4", "1.5", "1.6", "1.7"}
CYCLONEDX_COMPONENT_TYPES = {
    "application", "container", "cryptographic-asset", "data", "device",
    "device-driver", "file", "firmware", "framework", "library", "machine-learning-model",
    "operating-system", "platform", "service",
}

# SPDX License List Data v3.29.0; generated from the immutable official tag.
# Source SHA-256: licenses.json=47d1cc681abe31166b342b6cc4aab13a6ba8ea5c48794697fe3bf8b1dbaf509a, exceptions.json=59ee7c150de521d635cbf9367b8cf543b8da6781ea3ac1c073d305524f84ada2
_SPDX_LICENSE_DATA = (
    "c-oCx+m_n8vVGUD?4gXtPG=>L#s-?ifP+oClQ#hZeA>6;O9Sic&n!tcHl1Xj2VApC_gkr|#KSoBnC<Z}iFdN(qYn?vaeU@@AwE+d"
    "f{~$?q84RPRLgDRXMwLv8o=``x%Rk{C!&_6mUJcTK}LI!(VzMAG57Lalgg|Xb)09+S$I5?=Oo?|4E(?W_36itNtKA~2pl*ebC!|6"
    "=7HA%eh^8r6}wmF1kRMov>gt@0T{Kw(m;(ie4cVIKE}03xKmZKu5^*#%2C6%s;`Nxn6npdUP`{tcfk2pa_iyYlYw4b6^Xd=Ag}6h"
    "SH?wc5@yM@I2pXD^CA{t-bA*Od+wIkqK<bJO7Cb&M2X2eq=hIe$(%&&_c5la5WADqU2Nf~g=#=W!7Hm^PFbAd9Up!9@&&k#tC~2<"
    "&|`QY%Va3oC7*8stDk=l!kns*Ny66~HeUITY8-ovkGPwN2K^+)`W?ePQJyh?foxhzNn3OvT*~^LEWpOe>Y?mMEYH<YbcoBU=1H8%"
    "yM*HPxXZHxjbWFFIK?g;rsoxJvfT;WOzu^Z-5*5DaHp=zc-yEQJ4kR7DaxVfDH!tpc<B!%i+mp+;+=S3-7@EkIKk#CuiVGk&F(vb"
    "49jLmjsAbru|G#2|Cs4nl%>e(>oCKrkQrYD^Y_wcx!v4}!{%-fQI!t`Vpm|aL)jsb7bW@FpYV@erKh~a(wjx;#2Z)hAMEvHxoUCb"
    "Jgp{*!J(40Z{zI9o!x7epA)%1%Cy}?y>-MoExr4VG0zUN%yQa`HEMcQ!i6Yb2WzG`Sy1M++;wXh=1sZ7hRFBwp696Xt|5~ulA1$L"
    "_C;HHjI)deaEKFA)wQZ+3KcQr__O_-m#<nXs#jj;TxR>cRFkPy;CN$H;8@7lw84PHiB!=T>(0hfmS<5ScU(t#Sz?xSp*}kzwyH}h"
    "QtqBaNt*m7`5{XzY|_Y;c%`ybI+v%E&)g31b6lVDrsfBUrg99j!P1wp$e<U+J_Wt0j)B<zyO`z`#~TY(sa0t9du-(9SmuWVbNR+|"
    ")NowK?^7&Ex`nFPbDBLqx$I=2R?&5WiHk{WfTER|H`nFkpSrk0Ru4Vi5O+3)vAYa(`8iRa>E+?xq{qx>s~!h^abq^2VLo;hFWp{;"
    "z*s}(@y6KwltHV8zcmcOpamR`(H(ES;~xjSeV2aUpb-t)TzbQ1Luc45c<VhssMd(4>vy8fw>#0}fVb}|w%=F$uD_7=`a77T!2}Iv"
    "XD~JQ^U|My!RQA=zaNp}NBnEh=O1s3-7h)ViT4A2H_F=(1J83cU9|n`dibEJ=lOkr1Jem$C&11~<Eo0^<ZoqGD+{4mNad+a6L;?W"
    "LpW-ob(2~*>9_^x?PR^=tEaY5$5=NeFtr#q9J0@vozA0hju}~Op#OKL4nXIS>E|xjbN=-Ty_RVlyu+l}N!Jv(Gq%v=?rLsE6|@Q|"
    "Xz5A;^l{_xRVx+Ap0ul>r0fdPxvDNvW5%exq_xuk<Dz0{Fm1B9zVfh;+VXRAx)k(;<Yh*7oS#<}tl0|tXEI|hYPw#CRMj~z_e`0j"
    "d3cTwHQ@@za9G3AhU#~ltz;gbn)}6r#_Y5-R^FSmN>VewChL`ES1$9`rqEh1j~99YgF7(J^H=UgUWY?iwW+wV%Je=-S$Cor*!x~y"
    "SMq-|HgR>eM5(FC)bPf$nFMn;#MejzBgIYzEc(0gV=Kk&mSa2a@&YX%>VDE$;-(uWY&=nmV;S!O+g3CfwZLMNM0B?GB&#v`f=$Y#"
    "&DLP@uPK|37i{W%<BsDhy1@GICpHbIPN2WE56;GF>su8_-1z$IuUiUx+Ms7bsx9vLZ&t@F<`J7lC@5moA<JV^Ve3Zc{18_sHZ4Ue"
    "u_Z2QP{wTMw3dYDsW?hDZAvVDePT0rO>R-eW*(bOjXG!nkFz&Hw>TUs<sbewp>-9zL=5(i1@F?{l^y23Pq#YorxvWgRd4;JYu111"
    "nst}^KHcuXpIWg0R=xd~uG#;kYxZ64Te@!rA->NM%+)qDVCa9?AVlykLNGH&&d~p~;FjI`O?G8A!hCeO>`b&ll4$B&d3S$E-^R3+"
    "|B#4dQ&mvSr$1#l<S>7SX|@@I-HyTTkHNklgZ<|**nb-XjvP4`N1VNsF2$Klai*Y)m90KY;yrzv<N{I(a`!8fr>S8cXY9$FKC-8|"
    "=fHz|3J?qU{K?QSjJzK8fp&H^n93}ZTO573jBjYLs?iITGRJM^@cF`-86J8o(eRsmgTKw6sO(jXpi24*Nz33nH5LEXoJ^JQtV;Q+"
    "aj;E`q^UF$E=+ee9mS*+a0AH%*3KF(-WKo*_n7AHa&;{H&Y=f*mz<JIckN59t`ZD@q5`$pk~@Co=D_GgYWi-p{8GvCtsS+>P|71}"
    "?MvQQVkEzvy1!&V-EY6igoqPv7zC~=<sxEYqlF*My`Ss}?jdrnC5@6j#YK_tN};c#(SWC&xkG@9&BqH~#M_F^-3^=Lnpd7I*)w@2"
    "Np(y%zQg8UXY}J0`Ri<s=-u4thI#1H4<2HiBmzdoJY03REjF0CJQm^@BY-zg5&yFoyziX<ng<G$DfLGHXBaTHjZz`t@=(@I!Sr5K"
    "U+%cr=>*HIsI;m172Sx{{q!|h&RzC3TrJsGk>Xdu0#%iL!;M^`s&C?7ia&nK8k|Tsa+b_@^cA70&HPq{`JE2)2TII;r^Sq9)Pb(Z"
    "Xi#KySHx~xvAb66K`ZusE4c3c)hb|qpiYJgu7c6aU?_$XbP{887|<ozqC-38|60H-hXwNG!)Qd2g)bkhhrZN8IdtJ-$(HgOMoC5e"
    "+T)hN;voe(Ezf8f{n(Gc!}L}SE4=Pw(gTLy7Kr)!an@W^#A!Y@40-+q78LnFOYu(J8fAY>K$zjL1?eC$zuCom!4cv}MZu@I1uC|f"
    "<L)C%iH_le`obU3G@%VlXewdfy@^CI-%c<f5nRlZz2Xr*LSTviFdBeFVf0PW=SC563tcH5id86ZkS9fR3d$k2felM6EXZ1^7~+;w"
    "hb<QK7j;7{7J8*Io639)!x=LoLr02QhNo?-A}MATZdcJF<k&w%^mmPK<m2-r(zPg`pcK{32Y4^)sbRG<7vfdUvpsbj(Ing<<G4gh"
    "jNQE01+emYMOPd&%9$<Vte^=0A}&{0fxONu{Me$w!$2G5?-)xn6iWv!*wUGY5ryY%K;^drjLk$W-SM1W6PE7GhgYo%FI^wM&|yo8"
    "$w6yG+Sy%FJ(O2d9xbB|caIQC5a#BVUivE?8&E)1PBXbgScxs$E||4g1{7?fbHBs{FC$#J)L0bI58G`i=twhCf#Gry5OBRI$QeFd"
    "U9x5ViuZ(NewJIdB;Sv%y4cssS+kYvMznw{*VnP4l?Olns?)u#?#yaBorn0goHpUs(`ARTMQ-e<rF~hKWu^mEs~N1wNW^EHG|j^y"
    "7MZ~DvJ8uvAKhS2YAYCFx>i068Qr8oR)_2keKbf_(W0)5)kD?|^`$+z-Jx5pj~)8dp?^B`8EC4niWNN5)`ch9jwRTPjW9H`6HaJZ"
    "bl<`@%}lP@&YEgo)e?pQBdxgbo^=7>U>xdB1BWzWn!<?{*<K{Lu9fIVtnS^w@q8AvVVeMUp?#wxh6{iN;Z(IAKv_oDLciB2Rj5V?"
    "F_}c4RJ7MH|7a$@K!XMd?Zc`I^5i<jHIC<oY=@`>EUFl`i4GEXiD8NZ#K%oVk2h79TGhu=hTqW_9ZON&=^$q0yP0A`jj4*m{*iq@"
    "H82g=Ptwixow7CuP18qEGu>-wg@p%#NDNz(eZ|(z_NqR-z^h2;ywF+HM0Fx`Ck_i|b3Rrzv}k0IWn?trGaqo_Z)QAL00kdei1^Y2"
    "I$$UY&1u2ZSM6(J77pS>q=3SOzx;|XElIeXquCYId}sz9hEH6t^pnEc0z*X-50Jg354!>zL!VKdxRCF|RG>u~voOTtK`P>;1N`#`"
    "%f4~yRmi+js1e7hxkbW6%HoR05r$-?+7W!p=;;}rM+?7vyumLlR6Q*Bc;}@-Hb+sqYl<?`4#SfY4}zejw5$)rz$;?m6~<zQSC7aE"
    ")+V_3z#^#b$PM@z4iSt@q|RD<CtAQFVbN;+eKS%0rr17<0*^i>ON2RzqMrfahL7<$8f%Sp<7k+Xe0}=if|Ti6iv2e@KG7*I_meB?"
    "M-T*t&v#V4aiMfKCMdO8#0VNLBu)uygF~BjgsXJIHrpnv8|`T-dA6pKu$Rcxwv|L<ZyM4&-s8RCQzwX48^RYAx&ung>tKiQJi3Lg"
    "oXL79J7WY%k>?5fu?nO0YQcV()A1ut@w>#Z`YF`-Y`gTMr{&KN|G*d|hC21|II`JuG+i#H*r@>Y6*bPhs^&R{|5dBU7VOc20qk*w"
    "vFECZ3&CDSBl@XlX7x5FwPV)4wRK(-Wuh1;xezsxieBPKTAgXp_w0rA)aOrXkzc}BY{!?q+%_t#Y_k_x<`?#Ih>z@rX5pt<jh{w4"
    "{OVC!?O#SW|3b~bq{NfbFPsFO6sKE6WNE8*@FV+0PK3y=d=<`HO8rb|Kn?wQ0F%OxaPQf6N6Uo61kZ?jvWkyza@dwavSj~l|BVYv"
    ">B7ez{={%vcL+YdRT`zQtTWm&d8wgOXw$rxjo7ZigZ~aePI}(L>>g4$=4@Z*g6-epVt+`PQkOJ3Ig{8(Od{$lwn}n1CuepHSNhJ~"
    "vLjw*OE`-c*v7}KDU!Tq$NF`P$SgbUVPmI^UKyb-woP(W>&IdoL)7{|`I}^kGV)Q^rw{gH%TYss@Mx8JKGJNBz7oZoZG!(2yv2LG"
    "T_4GgOsf3A;NgjX8L6kDjB|j@I@M{yQuJQwvb2bMF<xSKCyVL0Lxwx1VCFvptjJOr@HJxnMRh2}5ri78SfTHoWG@O<!c5<@Dnn~o"
    "ePHN99X2H%jagNl_^Z5DJB+nDmsoFBpRgY>8#TfRU?ED`*~agju=C|PMpz#IWVqnwtM}}J!rL4gk)AZMN%^lh%im%+`j;e!eR(Mg"
    "cKJ8YQ%26}eQe|Z08^Bqfd"
)
_SPDX_EXCEPTION_DATA = (
    "c-oCs+j5&Q5Pj!glmVPh(nkkh;s)Eq*!4``1PEKT(5jN~<?k!(nP$PH?F+Wf(XP(!9>L)GD`DrHDa%26Lt~ld=FkTTE)QJph|*vo"
    "2VwB0x`AZ*0g>>*?}G>Z@Qz7$O8yt>v&f3(8oU>7-2V?WcnBZw!0;jTUI45BS}={|N7G^ar#=M7voz_H6bWjKqIUm(I5B(B)*JC1"
    "U)6$<$udpR2@OukyCv~7iCgjmZxXNH(AEh(9hf1jT3TK)On|f<G1Dme7eXXh)`17^)ahclM#-m(<$Ubcv{HhZ7O^2YREmpMCz~?d"
    "pvb`4sxTy@hV`&qI!~1}@8j;r&}9tN`YzNJ5;S$DRx?^*O(gXFFP{<5-n24%oC{#*=9Ey&TwAv+n|m`^FvZ-%0TmQ=yAf|ZqJ`6z"
    "X{vdPi9atXZaDY+KF8pX`4BI8W&uPCk@cCFi^-`Fw*fBTa5GTbJIg3qw;<m^v=TR;-RoJp`o4|ci1RS3GI8m|CedmfqU}jaK$B!N"
    "A!WyWe(>Z!2T$I+JH|zl*<v*tHjREn;ZAPX?!MQ53Eoe(vv0K;UE4B=TUY7o@^rWuqjS>N_y%8p125gqFv}ZHllVHh3)vXD!=_3V"
    "6Qb!-qT$G!_eEQtm!5cu#CdCW=CTBYY+gn<U?cgFX+uTlEpRPsd){(cz{zy(W3DgC8fI%nnjxhr&d4G#ofa@Hs1prV^^Cc!u;5ZN"
    "rtMvZovg`EdZZQQ*nE4cGk5V2!b3%NN?bQ1_;BPpC*Z^ygA=$bqBOr&@j3_xh^csqSLX_cHHnT&_9RpwE{f{b8%9IrJ{4$fE;8@w"
    "g!v`7WyaP8hn~hdLP~}Yt4Zd*9GzAjO=(rpBkddnN7>Va-vp_ir2"
)
SPDX_LICENSE_IDS = frozenset(
    zlib.decompress(base64.b85decode(_SPDX_LICENSE_DATA)).decode("ascii").splitlines()
)
SPDX_EXCEPTION_IDS = frozenset(
    zlib.decompress(base64.b85decode(_SPDX_EXCEPTION_DATA)).decode("ascii").splitlines()
)
if (
    len(SPDX_LICENSE_IDS) != 740
    or len(SPDX_EXCEPTION_IDS) != 86
    or hashlib.sha256("\n".join(sorted(SPDX_LICENSE_IDS)).encode("ascii")).hexdigest()
    != "5ab8472b49411f847aa66aef4571e8a48a07522583028440da24f5c8fe0574ea"
    or hashlib.sha256("\n".join(sorted(SPDX_EXCEPTION_IDS)).encode("ascii")).hexdigest()
    != "345ed515e4b5ca426ef04c96805b9aa5b3cc4ad35b5fb471e9488a5359e5e21b"
):
    raise RuntimeError("Embedded SPDX identifier data failed integrity validation")

TEMPLATE_FIELDS = {
    "FLASK_ENV", "ADMIN_PASSWORD", "SECRET_KEY", "GMAIL_TOKEN_ENCRYPTION_KEY",
    "GMAIL_HTTP_TIMEOUT_SECONDS",
    "GOOGLE_MANAGER_IMAGE", "PUBLIC_DOMAIN", "RECHARGE_MODE", "RECHARGE_UPSTREAM_URL",
    "RECHARGE_UPSTREAM_ALLOWED_HOSTS", "DATABASE_URL", "GMAIL_CLIENT_SECRET_FILE",
    "GMAIL_REDIRECT_URI", "GMAIL_PUBSUB_TOPIC", "GMAIL_PUBSUB_VERIFICATION_TOKEN",
    "PROXY", "HEADLESS", "TRUSTED_PROXY_CIDRS", "GUNICORN_BIND", "GUNICORN_WORKERS",
    "GUNICORN_THREADS", "GUNICORN_TIMEOUT", "GUNICORN_LOG_LEVEL",
    "CDK_ENABLED", "CDK_ACTIVE_KEY_ID", "CDK_ENCRYPTION_KEYS", "CDK_LOOKUP_KEYS",
    "CDK_SMTP_HOST", "CDK_SMTP_PORT", "CDK_SMTP_FROM", "CDK_SMTP_USERNAME", "CDK_SMTP_PASSWORD",
}

ERROR_MESSAGES = {
    "ARGUMENT_INVALID": "A release bundle argument is invalid.",
    "CI_URL_REQUIRED": "A trusted CI run URL is required for a formal bundle.",
    "CI_URL_INVALID": "The CI run URL must be a credential-free HTTPS URL.",
    "DIRTY_WORKTREE": "A formal bundle requires a clean Git worktree.",
    "EVIDENCE_INVALID": "Release evidence is missing or structurally invalid.",
    "EVIDENCE_MISMATCH": "Release evidence does not identify the requested image digest.",
    "FILES_INVALID": "A required public release file or lockfile is invalid.",
    "GIT_INVALID": "The Git release identity could not be verified.",
    "LICENSE_INCOMPLETE": "A formal bundle requires license metadata for every SBOM component.",
    "OUTPUT_INVALID": "The output must be a new safe directory inside the project.",
    "SCAN_BLOCKED": "High, critical, or suppressed vulnerability findings block a formal bundle.",
    "WRITE_FAILED": "The release bundle could not be written safely.",
}


class BundleError(Exception):
    """Expected failure with a stable, non-sensitive public message."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.public_message = ERROR_MESSAGES[code]


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_lstat(path):
    try:
        path.lstat()
        return True
    except OSError:
        return False


def _is_link_like(path, file_stat=None):
    if file_stat is not None and stat.S_ISLNK(file_stat.st_mode):
        return True
    try:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )
    except OSError:
        return True


def _reject_symlink_components(root, relative_path, error_code):
    cursor = root
    for part in Path(relative_path).parts:
        cursor = cursor / part
        try:
            file_stat = cursor.lstat()
            if _is_link_like(cursor, file_stat):
                raise BundleError(error_code)
        except OSError as error:
            raise BundleError(error_code) from error


def _regular_file(root, relative_path, *, evidence=False):
    if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise BundleError("FILES_INVALID")
    error_code = "EVIDENCE_INVALID" if evidence else "FILES_INVALID"
    _reject_symlink_components(root, relative_path, error_code)
    candidate = root / relative_path
    try:
        file_stat = candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise BundleError(error_code) from error
    if (
        _is_link_like(candidate, file_stat)
        or not stat.S_ISREG(file_stat.st_mode)
        or not _inside(resolved, root)
    ):
        raise BundleError(error_code)
    if evidence and (file_stat.st_size <= 0 or file_stat.st_size > MAX_EVIDENCE_BYTES):
        raise BundleError("EVIDENCE_INVALID")
    return resolved


def _read_snapshot(root, relative_path, *, evidence=False):
    error_code = "EVIDENCE_INVALID" if evidence else "FILES_INVALID"
    resolved = _regular_file(root, relative_path, evidence=evidence)
    maximum = MAX_EVIDENCE_BYTES if evidence else MAX_PUBLIC_FILE_BYTES
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened_stat = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_size > maximum:
                raise BundleError(error_code)
            if evidence and opened_stat.st_size <= 0:
                raise BundleError(error_code)
            value = stream.read(maximum + 1)
            if len(value) > maximum or stream.read(1):
                raise BundleError(error_code)
        current_stat = resolved.lstat()
        _reject_symlink_components(root, resolved.relative_to(root), error_code)
        if _is_link_like(resolved, current_stat) or (
            opened_stat.st_dev,
            opened_stat.st_ino,
        ) != (current_stat.st_dev, current_stat.st_ino):
            raise BundleError(error_code)
    except BundleError:
        raise
    except (OSError, ValueError) as error:
        raise BundleError(error_code) from error
    return value


def _evidence_file(root, raw_path):
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise BundleError("EVIDENCE_INVALID") from error
    _regular_file(root, relative, evidence=True)
    return relative


def _validate_output(root, raw_output, draft):
    raw_path = Path(raw_output)
    if ".." in raw_path.parts:
        raise BundleError("OUTPUT_INVALID")
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    if not SAFE_OUTPUT_NAME.fullmatch(candidate.name):
        raise BundleError("OUTPUT_INVALID")
    if draft != candidate.name.startswith("BLOCKED-"):
        raise BundleError("OUTPUT_INVALID")
    if _has_lstat(candidate):
        raise BundleError("OUTPUT_INVALID")
    try:
        parent = candidate.parent.resolve(strict=True)
    except OSError as error:
        raise BundleError("OUTPUT_INVALID") from error
    if not parent.is_dir() or not _inside(parent, root):
        raise BundleError("OUTPUT_INVALID")
    _reject_symlink_components(root, parent.relative_to(root), "OUTPUT_INVALID")
    return parent / candidate.name


def _load_json(value):
    try:
        return json.loads(value.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise BundleError("EVIDENCE_INVALID") from error


def _parse_image(image):
    if not isinstance(image, str) or image != image.strip():
        raise BundleError("ARGUMENT_INVALID")
    match = IMAGE_PATTERN.fullmatch(image)
    if not match or ".." in match.group("repository") or match.group("repository").endswith("/"):
        raise BundleError("ARGUMENT_INVALID")
    return match.group("repository"), match.group("digest")


def _is_public_hostname(value):
    if not value or value != value.strip() or value.endswith("."):
        return False
    lowered = value.lower()
    if (
        any(marker in lowered for marker in PLACEHOLDER_MARKERS)
        or "://" in lowered
        or ":" in lowered
    ):
        return False
    try:
        ipaddress.ip_address(lowered)
        return False
    except ValueError:
        pass
    labels = lowered.split(".")
    return (
        len(labels) >= 2
        and all(HOST_LABEL.fullmatch(label) for label in labels)
        and labels[-1] not in {"invalid", "example", "test", "local", "localhost"}
    )


def _validate_ci_url(ci_url, *, required):
    if not ci_url:
        if required:
            raise BundleError("CI_URL_REQUIRED")
        return None
    if not isinstance(ci_url, str) or ci_url != ci_url.strip() or len(ci_url) > 2048:
        raise BundleError("CI_URL_INVALID")
    try:
        parsed = urlsplit(ci_url)
        port = parsed.port
    except ValueError as error:
        raise BundleError("CI_URL_INVALID") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not _is_public_hostname(parsed.hostname.lower().rstrip("."))
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise BundleError("CI_URL_INVALID")
    return ci_url


def _git_environment():
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }


def _git_state(root):
    git_environment = _git_environment()
    try:
        top = subprocess.run(
            ["git", "-C", os.fspath(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=git_environment,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
        git_sha = subprocess.run(
            ["git", "-C", os.fspath(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="ascii",
            env=git_environment,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", os.fspath(root), "status", "--porcelain=v1", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=git_environment,
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        raise BundleError("GIT_INVALID") from error
    try:
        top_path = Path(top).resolve(strict=True)
    except OSError as error:
        raise BundleError("GIT_INVALID") from error
    if top_path != root or not re.fullmatch(r"[0-9a-f]{40,64}", git_sha):
        raise BundleError("GIT_INVALID")
    return {"sha": git_sha, "dirty": bool(status)}


def _git_blob(root, git_sha, relative_path):
    try:
        return subprocess.run(
            ["git", "-C", os.fspath(root), "show", f"{git_sha}:{relative_path}"],
            check=True,
            capture_output=True,
            env=_git_environment(),
            timeout=GIT_TIMEOUT_SECONDS,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise BundleError("GIT_INVALID") from error


def _suppressed_count(value):
    count = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower().startswith("suppressed"):
                if isinstance(item, list):
                    count += len(item)
                elif item not in (None, False, "", {}, []):
                    count += 1
            else:
                count += _suppressed_count(item)
    elif isinstance(value, list):
        count += sum(_suppressed_count(item) for item in value)
    return count


def _validate_scan(document, image, architecture):
    if not isinstance(document, dict):
        raise BundleError("EVIDENCE_INVALID")
    try:
        created_at = datetime.fromisoformat(document.get("CreatedAt", ""))
    except (TypeError, ValueError):
        created_at = None
    if (
        document.get("SchemaVersion") != 2
        or not isinstance(document.get("ArtifactName"), str)
        or not document["ArtifactName"].strip()
        or document.get("ArtifactType") != "container_image"
        or created_at is None
        or created_at.tzinfo is None
        or not isinstance(document.get("Trivy"), dict)
        or not isinstance(document["Trivy"].get("Version"), str)
        or not document["Trivy"]["Version"].strip()
        or not isinstance(document.get("Metadata"), dict)
        or not isinstance(document.get("Results"), list)
        or not document["Results"]
    ):
        raise BundleError("EVIDENCE_INVALID")
    metadata = document["Metadata"]
    repo_digests = metadata.get("RepoDigests")
    if not isinstance(repo_digests, list) or not repo_digests:
        raise BundleError("EVIDENCE_INVALID")
    if image not in repo_digests or any(
        not isinstance(item, str) or not IMAGE_PATTERN.fullmatch(item)
        for item in repo_digests
    ):
        raise BundleError("EVIDENCE_MISMATCH")
    image_config = metadata.get("ImageConfig")
    if not isinstance(image_config, dict):
        raise BundleError("EVIDENCE_INVALID")
    if image_config.get("architecture") != architecture or image_config.get("os") != "linux":
        raise BundleError("EVIDENCE_MISMATCH")

    severity_counts = Counter({severity: 0 for severity in KNOWN_SEVERITIES})
    result_types = Counter()
    package_count = 0
    for result in document["Results"]:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("Target"), str)
            or not result["Target"].strip()
            or not isinstance(result.get("Class"), str)
            or not result["Class"].strip()
            or not isinstance(result.get("Type"), str)
            or not result["Type"].strip()
            or not isinstance(result.get("Packages"), list)
        ):
            raise BundleError("EVIDENCE_INVALID")
        for package in result["Packages"]:
            if (
                not isinstance(package, dict)
                or not isinstance(package.get("Name"), str)
                or not package["Name"].strip()
            ):
                raise BundleError("EVIDENCE_INVALID")
        package_count += len(result["Packages"])
        vulnerabilities = result.get("Vulnerabilities", [])
        if vulnerabilities is None:
            vulnerabilities = []
        if not isinstance(vulnerabilities, list):
            raise BundleError("EVIDENCE_INVALID")
        result_types[result["Type"]] += 1
        for finding in vulnerabilities:
            if (
                not isinstance(finding, dict)
                or not isinstance(finding.get("VulnerabilityID"), str)
                or not finding["VulnerabilityID"].strip()
            ):
                raise BundleError("EVIDENCE_INVALID")
            severity = finding.get("Severity")
            if severity not in KNOWN_SEVERITIES:
                raise BundleError("EVIDENCE_INVALID")
            severity_counts[severity] += 1

    if package_count == 0:
        raise BundleError("EVIDENCE_INVALID")

    suppressed = _suppressed_count(document)
    return {
        "severity_counts": dict(sorted(severity_counts.items())),
        "high_critical_count": severity_counts["HIGH"] + severity_counts["CRITICAL"],
        "suppressed_findings_count": suppressed,
        "result_count": len(document["Results"]),
        "result_types": dict(sorted(result_types.items())),
    }


_SPDX_TOKEN = re.compile(
    r"DocumentRef-[A-Za-z0-9.-]+:LicenseRef-[A-Za-z0-9.-]+"
    r"|LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9][A-Za-z0-9.+-]*|\(|\)"
)
_LICENSE_REFERENCE = re.compile(
    r"^(?:DocumentRef-[A-Za-z0-9.-]+:)?LicenseRef-[A-Za-z0-9.-]+$"
)


def _valid_license_identifier(value):
    return value in SPDX_LICENSE_IDS or bool(_LICENSE_REFERENCE.fullmatch(value))


def _valid_spdx_expression(expression):
    if not isinstance(expression, str) or not expression or expression != expression.strip():
        return False
    tokens = _SPDX_TOKEN.findall(expression)
    if " ".join(tokens) != " ".join(expression.replace("(", " ( ").replace(")", " ) ").split()):
        return False
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def primary():
        nonlocal position
        token = peek()
        if token == "(":
            position += 1
            if not disjunction() or peek() != ")":
                return False
            position += 1
            return True
        if (
            token is None
            or token in {"AND", "OR", "WITH", ")"}
            or not _valid_license_identifier(token)
        ):
            return False
        position += 1
        return True

    def with_exception():
        nonlocal position
        if not primary():
            return False
        if peek() == "WITH":
            position += 1
            token = peek()
            if token not in SPDX_EXCEPTION_IDS:
                return False
            position += 1
        return True

    def conjunction():
        nonlocal position
        if not with_exception():
            return False
        while peek() == "AND":
            position += 1
            if not with_exception():
                return False
        return True

    def disjunction():
        nonlocal position
        if not conjunction():
            return False
        while peek() == "OR":
            position += 1
            if not conjunction():
                return False
        return True

    return bool(tokens) and disjunction() and position == len(tokens)


def _license_values(raw_licenses):
    if not isinstance(raw_licenses, list) or not raw_licenses:
        return None
    values = []
    for choice in raw_licenses:
        if not isinstance(choice, dict) or ("license" in choice) == ("expression" in choice):
            raise BundleError("EVIDENCE_INVALID")
        if "expression" in choice:
            expression = choice["expression"]
            if not _valid_spdx_expression(expression):
                raise BundleError("EVIDENCE_INVALID")
            values.append({"expression": expression})
            continue
        license_data = choice["license"]
        if not isinstance(license_data, dict):
            raise BundleError("EVIDENCE_INVALID")
        identifier = license_data.get("id")
        name = license_data.get("name")
        if not isinstance(identifier, str) or not identifier.strip():
            identifier = None
        elif identifier not in SPDX_LICENSE_IDS:
            raise BundleError("EVIDENCE_INVALID")
        if not isinstance(name, str) or not name.strip():
            name = None
        elif len(name) > 512 or any(ord(character) < 32 for character in name):
            raise BundleError("EVIDENCE_INVALID")
        if not identifier and not name:
            raise BundleError("EVIDENCE_INVALID")
        value = {}
        if identifier:
            value["id"] = identifier
        if name:
            value["name"] = name
        values.append(value)
    return values


def _validate_sbom(document, repository, digest, architecture):
    if (
        not isinstance(document, dict)
        or document.get("bomFormat") != "CycloneDX"
        or document.get("specVersion") not in CYCLONEDX_VERSIONS
        or not isinstance(document.get("version"), int)
        or isinstance(document.get("version"), bool)
        or document["version"] < 1
        or not isinstance(document.get("metadata"), dict)
        or not isinstance(document.get("components"), list)
        or not document["components"]
    ):
        raise BundleError("EVIDENCE_INVALID")
    component = document["metadata"].get("component")
    if not isinstance(component, dict) or component.get("type") != "container":
        raise BundleError("EVIDENCE_INVALID")
    purl = component.get("purl")
    if not isinstance(purl, str):
        raise BundleError("EVIDENCE_MISMATCH")
    try:
        parsed_purl = urlsplit(purl)
        qualifiers = parse_qs(parsed_purl.query, strict_parsing=True)
    except ValueError as error:
        raise BundleError("EVIDENCE_MISMATCH") from error
    expected_name = repository.rsplit("/", 1)[1]
    if (
        parsed_purl.scheme != "pkg"
        or parsed_purl.netloc
        or parsed_purl.fragment
        or unquote(parsed_purl.path) != f"oci/{expected_name}@sha256:{digest}"
        or qualifiers.get("repository_url") != [repository]
        or qualifiers.get("arch") != [architecture]
    ):
        raise BundleError("EVIDENCE_MISMATCH")

    inventory = []
    missing = []
    seen_refs = set()
    pending = [(item, 1) for item in reversed(document["components"])]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_SBOM_DEPTH or len(inventory) >= MAX_SBOM_COMPONENTS:
            raise BundleError("EVIDENCE_INVALID")
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"].strip()
            or item.get("type") not in CYCLONEDX_COMPONENT_TYPES
            or (
                item.get("version") is not None
                and (not isinstance(item["version"], str) or not item["version"].strip())
            )
        ):
            raise BundleError("EVIDENCE_INVALID")
        reference = item.get("bom-ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference or reference in seen_refs:
                raise BundleError("EVIDENCE_INVALID")
            seen_refs.add(reference)
        licenses = _license_values(item.get("licenses"))
        entry = {
            "bom_ref": reference,
            "name": item["name"],
            "type": item.get("type"),
            "version": item.get("version"),
            "licenses": licenses or [],
        }
        inventory.append(entry)
        if not licenses:
            missing.append(reference or f"component-index-{len(inventory) - 1}")
        children = item.get("components", [])
        if not isinstance(children, list):
            raise BundleError("EVIDENCE_INVALID")
        pending.extend((child, depth + 1) for child in reversed(children))
    return {
        "schema_version": 1,
        "component_count": len(inventory),
        "components_with_license_metadata": len(inventory) - len(missing),
        "missing_license_metadata_count": len(missing),
        "license_metadata_complete": not missing,
        "compliance_review_status": "NOT_REVIEWED",
        "compliance_approved": False,
        "missing_component_refs": missing,
        "components": inventory,
    }


def _verify_template_is_non_secret(value):
    try:
        text = value.decode("utf-8-sig")
    except UnicodeError as error:
        raise BundleError("FILES_INVALID") from error
    fields = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if raw_line != line or "=" not in line:
            raise BundleError("FILES_INVALID")
        field, field_value = line.split("=", 1)
        if field not in TEMPLATE_FIELDS or field in fields:
            raise BundleError("FILES_INVALID")
        fields[field] = field_value
    if set(fields) != TEMPLATE_FIELDS:
        raise BundleError("FILES_INVALID")
    exact_values = {
        "ADMIN_PASSWORD": "CHANGE_ME",
        "SECRET_KEY": "CHANGE_ME",
        "GMAIL_TOKEN_ENCRYPTION_KEY": "CHANGE_ME",
        "GMAIL_HTTP_TIMEOUT_SECONDS": "30",
        "CDK_ENABLED": "0",
        "CDK_ACTIVE_KEY_ID": "v1",
        "CDK_ENCRYPTION_KEYS": "",
        "CDK_LOOKUP_KEYS": "",
        "CDK_SMTP_HOST": "",
        "CDK_SMTP_PORT": "465",
        "CDK_SMTP_FROM": "",
        "CDK_SMTP_USERNAME": "",
        "CDK_SMTP_PASSWORD": "",
        "GOOGLE_MANAGER_IMAGE": (
            "registry.example.invalid/google-manager@sha256:CHANGE_ME"
        ),
        "DATABASE_URL": "",
        "GMAIL_PUBSUB_VERIFICATION_TOKEN": "",
        "PROXY": "",
    }
    if any(fields.get(field) != expected for field, expected in exact_values.items()):
        raise BundleError("FILES_INVALID")


def _write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _readme(draft, manifest):
    if draft:
        heading = "# BLOCKED - DO NOT DEPLOY"
        status = "This draft is evidence for review only and is not deployable."
    else:
        heading = "# Release bundle - NOT APPROVED FOR DEPLOYMENT"
        status = "Preparation checks passed, but production approval still requires manual sign-off."
    blockers = manifest["blockers"] or ["None recorded by preparation checks."]
    lines = [
        heading,
        "",
        status,
        "",
        "The bundle contains no production .env, credentials, database, encryption key, or runtime data.",
        "It does not deploy, push, pull, or inspect Docker. RELEASE-SIGNOFF.md remains NO-GO.",
        "The Git executable, PATH, CI runner, and CI URL provenance are trust boundaries;",
        "an authorized reviewer must verify them independently.",
        "",
        "## Scope",
        "",
        "CDK workbench only. Cash payments, payment channels, and payment callbacks are excluded.",
        "",
        "## Recorded blockers",
        "",
    ]
    lines.extend(f"- {blocker}" for blocker in blockers)
    lines.extend([
        "",
        "Verify SHA256SUMS before review. Create the protected production .env and credentials",
        "separately on the target server; never add them to this directory.",
        "",
    ])
    return "\n".join(lines)


def _signoff():
    return (
        "# Release Sign-off\n\n"
        "Status: NO-GO\n\n"
        "This tool never grants production approval. An authorized release owner must review "
        "the evidence, target-server preflight, backup restore drill, monitoring, and rollback "
        "plan before changing this decision outside the generated bundle.\n"
    )


def _build_release_bundle(
    *, image, scan, sbom, output, platform, ci_url=None, draft=False,
    project_root=None, generated_at=None,
):
    """Validate evidence and create a new release preparation directory."""
    root = Path(project_root or PROJECT_ROOT).resolve(strict=True)
    repository, digest = _parse_image(image)
    if platform not in SUPPORTED_PLATFORMS:
        raise BundleError("ARGUMENT_INVALID")
    architecture = platform.split("/", 1)[1]
    ci_url = _validate_ci_url(ci_url, required=not draft)
    output_path = _validate_output(root, output, draft)
    scan_relative = _evidence_file(root, scan)
    sbom_relative = _evidence_file(root, sbom)

    public_snapshots = {
        relative: _read_snapshot(root, relative) for relative in PUBLIC_FILES
    }
    lockfile_snapshots = {
        relative: _read_snapshot(root, relative) for relative in LOCKFILES
    }
    scan_snapshot = _read_snapshot(root, scan_relative, evidence=True)
    sbom_snapshot = _read_snapshot(root, sbom_relative, evidence=True)

    scan_summary = _validate_scan(_load_json(scan_snapshot), image, architecture)
    license_inventory = _validate_sbom(
        _load_json(sbom_snapshot), repository, digest, architecture
    )
    git_state = _git_state(root)
    if not draft and not git_state["dirty"]:
        public_snapshots = {
            relative: _git_blob(root, git_state["sha"], relative)
            for relative in PUBLIC_FILES
        }
        lockfile_snapshots = {
            relative: _git_blob(root, git_state["sha"], relative)
            for relative in LOCKFILES
        }
    _verify_template_is_non_secret(public_snapshots["deploy/env.production.example"])

    blockers = []
    if git_state["dirty"]:
        blockers.append("dirty_git_worktree")
    if not ci_url:
        blockers.append("ci_run_url_missing")
    if scan_summary["high_critical_count"]:
        blockers.append("high_or_critical_vulnerabilities_present")
    if scan_summary["suppressed_findings_count"]:
        blockers.append("suppressed_vulnerability_findings_present")
    if license_inventory["missing_license_metadata_count"]:
        blockers.append("sbom_license_metadata_incomplete")

    if not draft:
        if git_state["dirty"]:
            raise BundleError("DIRTY_WORKTREE")
        if scan_summary["high_critical_count"] or scan_summary["suppressed_findings_count"]:
            raise BundleError("SCAN_BLOCKED")
        if license_inventory["missing_license_metadata_count"]:
            raise BundleError("LICENSE_INCOMPLETE")

    timestamp = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    file_hashes = {
        relative: _sha256_bytes(value) for relative, value in public_snapshots.items()
    }
    lockfile_hashes = {
        relative: _sha256_bytes(value) for relative, value in lockfile_snapshots.items()
    }
    evidence_hashes = {
        "evidence/trivy.json": _sha256_bytes(scan_snapshot),
        "evidence/sbom.cdx.json": _sha256_bytes(sbom_snapshot),
    }

    staging = output_path.parent / f".release-bundle-tmp-{uuid.uuid4().hex}"
    try:
        staging.mkdir(mode=0o700)
        for relative, value in public_snapshots.items():
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(value)
        evidence_dir = staging / "evidence"
        evidence_dir.mkdir()
        (evidence_dir / "trivy.json").write_bytes(scan_snapshot)
        (evidence_dir / "sbom.cdx.json").write_bytes(sbom_snapshot)

        inventory_path = staging / "LICENSE-INVENTORY.json"
        _write_json(inventory_path, license_inventory)
        manifest = {
            "schema_version": 1,
            "release_status": "BLOCKED" if draft else "AWAITING_MANUAL_SIGNOFF",
            "production_approved": False,
            "signoff_decision": "NO-GO",
            "generated_at": timestamp,
            "scope": {
                "included": "CDK workbench",
                "excluded": ["cash payments", "payment channels", "payment callbacks"],
            },
            "git": git_state,
            "platform": platform,
            "image": image,
            "ci_run_url": ci_url,
            "scan": {
                **scan_summary,
                "evidence_path": "evidence/trivy.json",
                "sha256": evidence_hashes["evidence/trivy.json"],
            },
            "sbom": {
                "component_count": license_inventory["component_count"],
                "missing_license_metadata_count": license_inventory[
                    "missing_license_metadata_count"
                ],
                "evidence_path": "evidence/sbom.cdx.json",
                "sha256": evidence_hashes["evidence/sbom.cdx.json"],
            },
            "license_inventory": {
                "path": "LICENSE-INVENTORY.json",
                "sha256": _sha256(inventory_path),
                "license_metadata_complete": not license_inventory[
                    "missing_license_metadata_count"
                ],
                "compliance_review_status": "NOT_REVIEWED",
                "compliance_approved": False,
            },
            "lockfile_sha256": dict(sorted(lockfile_hashes.items())),
            "public_file_sha256": dict(sorted(file_hashes.items())),
            "blockers": blockers,
        }
        _write_json(staging / "manifest.json", manifest)
        (staging / "README.md").write_text(
            _readme(draft, manifest), encoding="utf-8", newline="\n"
        )
        (staging / "RELEASE-SIGNOFF.md").write_text(
            _signoff(), encoding="utf-8", newline="\n"
        )

        expected = set(PUBLIC_FILES) | {
            "evidence/trivy.json",
            "evidence/sbom.cdx.json",
            "LICENSE-INVENTORY.json",
            "manifest.json",
            "README.md",
            "RELEASE-SIGNOFF.md",
        }
        actual = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        }
        if actual != expected or any(path.is_symlink() for path in staging.rglob("*")):
            raise BundleError("FILES_INVALID")

        checksum_lines = []
        for relative in sorted(actual):
            checksum_lines.append(f"{_sha256(staging / relative)}  {relative}")
        (staging / "SHA256SUMS").write_text(
            "\n".join(checksum_lines) + "\n", encoding="ascii", newline="\n"
        )
        # mkdir is the portable no-overwrite primitive for a directory.  A crash
        # may leave an incomplete directory, which future runs safely reject.
        output_path.mkdir(mode=0o700)
        try:
            for child in staging.iterdir():
                child.replace(output_path / child.name)
            staging.rmdir()
        except Exception:
            shutil.rmtree(output_path, ignore_errors=True)
            raise
    except BundleError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as error:
        shutil.rmtree(staging, ignore_errors=True)
        raise BundleError("WRITE_FAILED") from error
    return output_path


def build_release_bundle(**options):
    """Convert every unexpected build failure into a stable redacted error."""
    try:
        return _build_release_bundle(**options)
    except BundleError:
        raise
    except Exception as error:
        raise BundleError("WRITE_FAILED") from error


def build_parser():
    parser = argparse.ArgumentParser(
        description="Build an offline, inspectable Docker deployment preparation bundle."
    )
    parser.add_argument("--image", required=True, help="Pinned repository@sha256 image reference.")
    parser.add_argument("--scan", required=True, help="Trivy JSON vulnerability report in this repo.")
    parser.add_argument("--sbom", required=True, help="CycloneDX JSON SBOM in this repo.")
    parser.add_argument("--output", required=True, help="New output directory inside this repo.")
    parser.add_argument("--platform", required=True, choices=SUPPORTED_PLATFORMS)
    parser.add_argument("--ci-url", help="Credential-free HTTPS URL for the successful CI run.")
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Create a BLOCKED-* review bundle even when release gates remain open.",
    )
    return parser


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    try:
        output = build_release_bundle(
            image=arguments.image,
            scan=arguments.scan,
            sbom=arguments.sbom,
            output=arguments.output,
            platform=arguments.platform,
            ci_url=arguments.ci_url,
            draft=arguments.draft,
        )
    except BundleError as error:
        print(f"ERROR {error.code}: {error.public_message}", file=sys.stderr)
        return 1
    except Exception:
        print(f"ERROR WRITE_FAILED: {ERROR_MESSAGES['WRITE_FAILED']}", file=sys.stderr)
        return 1
    print(f"Release preparation bundle created: {output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
