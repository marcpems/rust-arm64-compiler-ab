extern crate a_dylib;

pub fn value() -> u64 {
    a_dylib::VALUE.load(std::sync::atomic::Ordering::SeqCst) + 1
}
