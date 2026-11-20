"use strict";

// Facebook ships a statically linked BoringSSL copy inside libcoldstart.so, and
// the process also maps other modules that export X509_verify_cert. A global
// export lookup resolves to whichever module wins the symbol race, which on
// emulator-5554 was NOT the copy Facebook actually calls (0 hits logged while
// the libcoldstart copy accepted 2). Enumerate every module and hook every
// export instead of hardcoding a module name.
//
// Every call site checks the return value against 1 (verified by disassembling
// all 8 call sites in libcoldstart.so), so forcing 1 accepts every chain.

let hookCount = 0;
let accepted = 0;
const hookedAddresses = {};
const MAX_ATTEMPTS = 40;
let attempt = 0;

function installAllX509Hooks() {
  let installed = 0;
  let modules = [];
  try {
    modules = Process.enumerateModules();
  } catch (e) {
    console.log("[facebook-unpin] module enumeration failed: " + e);
    return 0;
  }

  for (const module of modules) {
    let address = null;
    try {
      // getExportByName is strict: it throws when the module does not export
      // the symbol. findExportByName silently falls back to the global lookup,
      // which made every unrelated module report the same address.
      address = module.getExportByName("X509_verify_cert");
    } catch (e) {
      continue;
    }
    if (address === null) {
      continue;
    }
    // Many Facebook merged libraries resolve to the same BoringSSL copy, so the
    // same address shows up under dozens of module names. Attach once per
    // address instead of once per module.
    const key = address.toString();
    if (hookedAddresses[key]) {
      continue;
    }
    try {
      Interceptor.attach(address, {
        onLeave(retval) {
          if (retval.toInt32() !== 1) {
            retval.replace(1);
          }
          accepted += 1;
          if (accepted <= 5) {
            console.log(
              "[facebook-unpin] accepted chain via " +
                module.name +
                " hit=" +
                accepted
            );
          }
        },
      });
      hookedAddresses[key] = true;
      hookCount += 1;
      installed += 1;
      console.log("[facebook-unpin] hook installed " + module.name + "@" + address);
    } catch (e) {
      console.log("[facebook-unpin] hook failed " + key + ": " + e);
    }
  }
  return installed;
}

const timer = setInterval(() => {
  attempt += 1;
  const installed = installAllX509Hooks();
  // Keep polling until something is hooked; new modules keep loading during
  // startup and a later attach would otherwise miss the real BoringSSL copy.
  if (installed > 0 && attempt >= 3) {
    clearInterval(timer);
    console.log(
      "[facebook-unpin] ready hooks=" + hookCount + " after " + attempt + " attempt(s)"
    );
  }
  if (attempt >= MAX_ATTEMPTS) {
    clearInterval(timer);
    if (hookCount === 0) {
      console.log("[facebook-unpin] gave up after " + attempt + " attempts hooks=0");
    } else {
      console.log(
        "[facebook-unpin] settled after " + attempt + " attempts hooks=" + hookCount
      );
    }
  }
}, 250);
