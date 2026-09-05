# third party notices

the harness code and checked-in transcript corpus use the Zigcho Public Use License in `LICENSE`.

the differential contract pins [`osuAkatsuki/bancho.py`](https://github.com/osuAkatsuki/bancho.py) at commit `0651b54c66daa839c1bb3998e4f9a8d1173e144d` (5.3.0). bancho.py is Copyright (c) 2019 cmyui and distributed under the MIT License.

bancho.py is not copied, bundled or started by this repository. the harness reads an operator-supplied clean checkout only when a full source attestation is requested.

Zigcho is also inspected from a separate checkout supplied with `--root` or `--zigcho-root`. its source remains under the licence shipped in [`zigcho/zigcho`](https://github.com/zigcho/zigcho).

## MIT licence text

the pinned bancho.py reference is supplied under this licence:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
# optional benchmark cipher

the isolated mixed-load runner uses py3rijndael 0.3.3 (MIT), by its upstream contributors, for Stable's 256-bit-block Rijndael score payloads. it is installed separately with a pinned source archive hash; normal conformance checks do not depend on it. source and license: https://github.com/meyt/py3rijndael. it is not vendored here.
