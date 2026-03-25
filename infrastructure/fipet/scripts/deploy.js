// scripts/deploy.js
const hre = require("hardhat");

async function main() {
  const provider = hre.ethers.provider;

  // Use node-managed account from geth --dev (via RPC)
  const accounts = await provider.send("eth_accounts", []);
  if (!accounts || accounts.length === 0) {
    throw new Error(
      "No RPC accounts available from node. Make sure geth --dev is running and RPC is reachable at http://127.0.0.1:8545."
    );
  }

  const deployerAddress = accounts[0];
  const deployer = await hre.ethers.getSigner(deployerAddress);

  const balance = await provider.getBalance(deployerAddress);
  const chainIdHex = await provider.send("eth_chainId", []);
  const chainId = Number(chainIdHex);

  console.log("Network chainId:", chainId);
  console.log("Deploying with account:", deployerAddress);
  console.log("Deployer balance:", hre.ethers.formatEther(balance), "ETH");

  const MyLabCoin = await hre.ethers.getContractFactory("MyLabCoin", deployer);
  const token = await MyLabCoin.deploy();

  await token.waitForDeployment();
  const tokenAddress = await token.getAddress();

  console.log("MyLabCoin deployed to:", tokenAddress);

  // Optional sanity checks
  const name = await token.name();
  const symbol = await token.symbol();
  const totalSupply = await token.totalSupply();
  const deployerTokenBalance = await token.balanceOf(deployerAddress);

  console.log("Token name:", name);
  console.log("Token symbol:", symbol);
  console.log("Total supply:", totalSupply.toString());
  console.log("Deployer token balance:", deployerTokenBalance.toString());
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
